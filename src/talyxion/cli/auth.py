"""``talyxion auth ...`` — login (OAuth device flow), logout, status."""
from __future__ import annotations

import platform
import socket
import sys
import time
import webbrowser
from typing import Any

import click
import httpx
from rich.console import Console
from rich.panel import Panel

from talyxion.cli._version import __cli_version__
from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    NotAuthenticatedError,
    TokenRevokedError,
    _api_prefix,
    base_url,
)
from talyxion.cli.keyring_store import (
    KeyringUnavailable,
    delete_device_token,
    load_device_token,
    load_token_meta,
    save_device_token,
)

console = Console()

# Flipped by the REPL dispatcher in :mod:`talyxion.cli.repl` before each
# slash invocation. Handlers can consult this to skip ``sys.exit`` on
# soft errors (e.g. "already authenticated") and just let control return
# to the REPL prompt naturally.
_INSIDE_REPL = False

# Browser-like User-Agent for the unauth device-flow endpoints. We sit
# behind Cloudflare's bot-fight WAF on production; httpx's default UA
# (``python-httpx/x.y.z``) trips the heuristic and earns a 403 before
# the request reaches Django. The string still identifies the CLI build
# (for our own analytics) but wraps it in a ``Mozilla/5.0`` envelope.
_BROWSER_UA = (
    f"Mozilla/5.0 (compatible; talyxion-cli/{__cli_version__}; "
    "+https://talyxion.com/platform/trading/setup/)"
)
_AUTH_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "X-App-Version": __cli_version__,
    "Accept": "application/json",
}


@click.group()
def auth():
    """Sign in / sign out of Talyxion."""


@auth.command("login")
@click.option("--label", default=None, help="Friendly name for this device (default: cli@<hostname>).")
@click.option("--open-browser", is_flag=True,
              help="Also auto-open the approval URL in your default browser. "
                   "Off by default — the URL printed in the terminal is the "
                   "primary affordance (Cmd/Ctrl-click works in most terminals).")
def auth_login(label: str | None, open_browser: bool):
    """Pair this machine with your Talyxion account via OAuth device flow.

    Step 1: prints a Cmd/Ctrl-clickable approval URL + a short user code.
    Step 2: you open the URL (terminal auto-link, copy-paste, or pass
    ``--open-browser`` to launch your default browser automatically).
    Step 3: polls every 5s until you click Approve, then saves the
    returned device token in the OS keyring.
    """
    if load_device_token():
        meta = load_token_meta() or {}
        console.print(
            f"[yellow]Already authenticated as[/yellow] [bold]{meta.get('email','?')}[/bold] "
            f"(prefix [cyan]{meta.get('prefix','?')}…[/cyan])."
        )
        console.print("Run [bold]/logout[/bold] first if you want to switch accounts.")
        if _INSIDE_REPL:
            return
        sys.exit(1)

    hostname = socket.gethostname()
    plat = f"{platform.system().lower()}-{platform.machine().lower()}"
    client_label = label or f"cli@{hostname}"

    # ── Phase 1: device/start ────────────────────────────────────
    try:
        r = httpx.post(
            f"{_api_prefix()}/auth/device/start/",
            json={
                "client_name": "talyxion-cli",
                "client_version": __cli_version__,
                "client_platform": plat,
            },
            headers=_AUTH_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        start = r.json()
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to start auth flow:[/red] {exc}")
        sys.exit(1)

    user_code: str = start["user_code"]
    device_code: str = start["device_code"]
    verification_uri: str = start["verification_uri"]
    verification_uri_complete: str = start.get("verification_uri_complete") or verification_uri
    interval: int = int(start.get("interval", 5))
    expires_in: int = int(start.get("expires_in", 600))

    # URL-first affordance — Rich ``[link=...]`` makes the URL Cmd/Ctrl-
    # clickable in modern terminals via OSC 8. Auto-open via
    # ``webbrowser.open`` is opt-in (``--open-browser`` flag) because it
    # surprised users on headless machines and SSH sessions; the link
    # itself is now visually impossible to miss.
    console.print(
        Panel.fit(
            f"[bold]1. Click the link below[/bold]\n\n"
            f"   [link={verification_uri_complete}][bold cyan underline]"
            f"{verification_uri_complete}[/bold cyan underline][/link]\n\n"
            f"[bold]2. Verify the code shown:[/bold]  "
            f"[bold yellow]{user_code}[/bold yellow]\n\n"
            f"[bold]3. Click Approve.[/bold]\n\n"
            f"[dim]Code expires in {expires_in // 60} minutes. "
            f"If your terminal doesn't make the link clickable, copy-paste it.[/dim]",
            title="Pair this device",
            border_style="cyan",
        )
    )
    if open_browser:
        try:
            webbrowser.open(verification_uri_complete)
        except Exception:
            pass

    # ── Phase 2: poll ────────────────────────────────────────────
    deadline = time.time() + expires_in
    with console.status("[cyan]Waiting for approval…[/cyan]", spinner="dots"):
        while time.time() < deadline:
            time.sleep(interval)
            try:
                pr = httpx.post(
                    f"{_api_prefix()}/auth/device/poll/",
                    json={"device_code": device_code},
                    headers=_AUTH_HEADERS,
                    timeout=15,
                )
                body = pr.json()
            except httpx.HTTPError as exc:
                console.print(f"[red]Poll failed:[/red] {exc} — retrying…")
                continue

            if pr.status_code == 200:
                token = body["access_token"]
                # Fetch whoami so we can show the user their identity.
                _save_and_announce(token, body.get("token_prefix", ""), body.get("label", client_label))
                return
            err = body.get("error", "")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = max(interval, interval + 2)
                continue
            if err == "access_denied":
                console.print("[red]Request denied in browser.[/red]")
                sys.exit(1)
            if err == "expired_token":
                console.print("[red]Code expired. Run `talyxion auth login` again.[/red]")
                sys.exit(1)
            console.print(f"[red]Unexpected response:[/red] {body}")
            sys.exit(1)

    console.print("[red]Timed out waiting for approval.[/red]")
    sys.exit(1)


def _save_and_announce(raw_token: str, prefix: str, label: str) -> None:
    # Stash token under provisional meta so DeviceTokenClient can use it.
    # Server-side the token is already minted + active; if we can't persist
    # it locally the user can revoke + retry. We never print the raw token.
    try:
        save_device_token(raw_token, {"prefix": prefix, "label": label})
    except KeyringUnavailable as exc:
        console.print(
            Panel.fit(
                f"[red]✗ Cannot save token to your OS keychain.[/red]\n\n"
                f"{exc}\n\n"
                f"[dim]The token was already minted on the server (prefix "
                f"{prefix}). After fixing the keychain, run "
                f"[bold]talyxion auth login[/bold] again — the new attempt "
                f"will create a fresh token; the orphan auto-expires in "
                f"60 days, or you can revoke it now at "
                f"https://talyxion.com/trading/devices/.[/dim]",
                title="Keychain unavailable",
                border_style="red",
            )
        )
        sys.exit(2)

    try:
        with DeviceTokenClient() as client:
            who = client.get("/trading/whoami/")["data"]
    except Exception as exc:
        # Token saved but whoami failed — still treat login as success.
        console.print(f"[yellow]Token saved but whoami failed:[/yellow] {exc}")
        return

    meta = {
        "user_id": who["user_id"],
        "email": who["email"],
        "tier": who["tier"],
        "prefix": prefix,
        "label": label,
    }
    try:
        save_device_token(raw_token, meta)
    except KeyringUnavailable:
        # The earlier save succeeded; this is just enriching the metadata.
        # Skip silently — DeviceTokenClient only needs the raw token.
        pass
    console.print(
        f"[green]✓ Authenticated as[/green] [bold]{who['email']}[/bold] "
        f"([cyan]{who['tier']}[/cyan] tier, token prefix [cyan]{prefix}[/cyan])."
    )


@auth.command("logout")
def auth_logout():
    """Revoke the current device token + remove it from the keyring."""
    if not load_device_token():
        console.print("[yellow]Not authenticated.[/yellow]")
        return
    try:
        with DeviceTokenClient() as client:
            client.post("/auth/device/revoke/", json={})
        console.print("[green]✓ Token revoked on server.[/green]")
    except TokenRevokedError:
        # Already revoked server-side — just clean up.
        pass
    except Exception as exc:
        console.print(f"[yellow]Server revoke failed[/yellow] ({exc}); cleaning up locally anyway.")
    delete_device_token()
    console.print("[green]✓ Keyring cleared.[/green]")


@auth.command("status")
def auth_status():
    """Show who you're authenticated as + capability summary."""
    if not load_device_token():
        console.print("[yellow]Not authenticated.[/yellow] Run [bold]talyxion auth login[/bold].")
        sys.exit(1)
    try:
        with DeviceTokenClient() as client:
            who = client.get("/trading/whoami/")["data"]
    except NotAuthenticatedError:
        console.print("[red]No token in keyring.[/red]")
        sys.exit(1)
    except TokenRevokedError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Failed to reach Talyxion:[/red] {exc}")
        sys.exit(1)

    caps = who.get("tier_caps", {})
    token = who.get("token", {})
    console.print(
        Panel.fit(
            f"[bold]Account:[/bold] {who['email']}\n"
            f"[bold]Tier:[/bold] [cyan]{who['tier']}[/cyan]\n"
            f"[bold]Token:[/bold] {token.get('label','?')} (prefix [cyan]{token.get('prefix','?')}[/cyan])\n"
            f"[bold]Server:[/bold] {base_url()}\n\n"
            f"[bold]Caps:[/bold]\n"
            f"  · active profiles: {caps.get('max_active_profiles','?')}\n"
            f"  · live profiles: {caps.get('max_live_profiles','?')}\n"
            f"  · max leverage: {caps.get('max_leverage','?')}×\n"
            f"  · exchanges: {', '.join(caps.get('allowed_exchanges', []))}\n"
            f"  · min cycle interval: {caps.get('min_cycle_interval_sec','?')}s\n"
            f"[bold]Local profiles:[/bold] {len(who.get('local_profile_ids', []))}",
            title="talyxion auth status",
            border_style="green",
        )
    )
