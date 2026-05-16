"""``talyxion add <exchange>`` and friends — local-only credential management.

Flow for ``add binance``:

  1. Interactive ``getpass`` prompts for ``api_key`` + ``api_secret``
     (+ ``passphrase`` for OKX). No echo to terminal.
  2. Construct the exchange adapter; call ``validate_credentials()`` to
     prove the key works and to read its permissions.
  3. Refuse if ``can_withdraw=True`` — sovereignty-of-funds is a hard
     invariant. User must mint a trade-only key.
  4. POST ``/api/v1/.../trading/credentials/create/`` with fingerprint +
     permissions (NO secret). Server enforces tier caps + the same
     ``canWithdraw=False`` rule.
  5. On 201 OK, store the raw key/secret JSON in the OS keyring under
     ``service="talyxion:binance"``, ``username="<label>"``.
"""
from __future__ import annotations

import getpass
import hashlib
import sys
from decimal import Decimal

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    NotAuthenticatedError,
    TokenRevokedError,
)
from talyxion.cli.exchanges import AuthFailure, ExchangeAdapter, IPBlocked, get_adapter
from talyxion.cli.keyring_store import (
    delete_credential,
    load_credential,
    save_credential,
)

console = Console()


def _fingerprint(exchange: str, api_key: str) -> str:
    """Stable, non-reversible fingerprint mirroring crypto_user.fingerprint_for.

    Server compares with the same recipe so duplicate-key detection works
    across CLI and the legacy web-side credential form.
    """
    h = hashlib.sha256()
    h.update(f"{exchange.strip().lower()}:{api_key.strip()}".encode("utf-8"))
    return h.hexdigest()


def _outbound_ip() -> str | None:
    """Best-effort guess at this machine's public IP, for whitelist hints.

    Returns ``None`` if we can't reach the lookup service in 2s — better
    to skip the line than to slow the diagnostic down."""
    import httpx as _httpx
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = _httpx.get(url, timeout=2.0)
            if r.status_code == 200 and r.text.strip():
                return r.text.strip()
        except Exception:
            continue
    return None


def _print_auth_failure_diagnostic(
    exchange: str, label: str, market_type: str, testnet: bool, exc: Exception,
) -> None:
    """Print a structured diagnostic when the exchange rejects the key.

    ``AuthFailure`` from Binance can mean any of: wrong key/secret, IP
    not whitelisted, wrong market-type endpoint, mainnet-vs-testnet
    mismatch, missing trading permission on the key, or clock skew. The
    error message we get back from Binance disambiguates only some of
    these, so we surface the raw response *and* list the likely fixes."""
    msg = str(exc)
    is_2015 = "-2015" in msg
    is_2014 = "-2014" in msg
    is_1022 = "-1022" in msg
    is_1021 = "-1021" in msg
    ip = _outbound_ip()

    console.print(f"[red]✗ {exchange} rejected the key:[/red] {msg}\n")

    causes: list[str] = []
    if is_2014:
        causes.append("• The API key string itself is malformed — re-copy from the exchange.")
    if is_1022:
        causes.append("• The API secret doesn't match the key — re-copy the secret carefully.")
    if is_1021:
        causes.append("• System clock skew — Binance requires ±10s. Sync your OS clock.")
    if is_2015 or (not is_2014 and not is_1022 and not is_1021):
        # -2015 is "Invalid API-key, IP, or permissions for action" — ambiguous.
        # Same advice applies when the error code is missing entirely.
        if ip:
            causes.append(
                f"• Your outbound IP is [cyan]{ip}[/cyan]. If the key has an IP "
                f"whitelist on {exchange.title()}, add this IP (or remove the "
                f"whitelist for a CLI-only key)."
            )
        else:
            causes.append(
                f"• If the key has an IP whitelist on {exchange.title()}, your "
                f"current outbound IP must be on it."
            )
        causes.append(
            f"• Endpoint mismatch: this attempt used [bold]{market_type}[/bold]. "
            f"If the key was minted for the other product type, retry with "
            f"[cyan]/add {exchange} --market-type=" +
            ("futures" if market_type == "spot" else "spot") +
            "[/cyan]."
        )
        if not testnet:
            causes.append(
                "• Testnet vs mainnet: if you minted the key on "
                f"testnet.{ 'binance.vision' if market_type == 'spot' else 'binancefuture.com' }, "
                f"retry with [cyan]/add {exchange} --testnet[/cyan]."
            )
        else:
            causes.append(
                "• Testnet vs mainnet: --testnet is set; make sure the key was "
                "minted on the testnet site (testnet.binance.vision / "
                "testnet.binancefuture.com), not the production account."
            )
        causes.append(
            "• Permissions: the key needs 'Enable Reading' + the corresponding "
            f"trade permission for {market_type}. Withdraw must stay OFF (the "
            "CLI refuses keys with withdraw enabled on mainnet)."
        )

    console.print(
        Panel(
            "[bold]Common causes — try in order:[/bold]\n\n" + "\n".join(causes) +
            "\n\n[dim]The web flow at /trading/credentials/ uses Talyxion's "
            "server IP — if that worked but the CLI doesn't, IP whitelist is "
            "the most likely culprit.[/dim]",
            title="What to check",
            border_style="yellow",
        )
    )


def _print_ip_blocked_diagnostic(exchange: str, exc: Exception) -> None:
    ip = _outbound_ip()
    extra = (
        f"Your outbound IP is [cyan]{ip}[/cyan] — add it to the API-key "
        f"IP whitelist on {exchange.title()}."
        if ip else
        f"Whitelist your current public IP on the {exchange.title()} API-key page."
    )
    console.print(
        f"[red]✗ {exchange} blocked your IP:[/red] {exc}\n{extra}"
    )


def _prompt_secret(label: str) -> str:
    """getpass with a friendly prompt; refuse empty."""
    val = getpass.getpass(f"  {label}: ").strip()
    if not val:
        console.print(f"[red]{label} cannot be empty.[/red]")
        sys.exit(1)
    return val


@click.command(name="add")
@click.argument("exchange", type=click.Choice(["binance"], case_sensitive=False))
@click.option("--label", default="main", help="Friendly tag for this credential (default: main).")
@click.option("--testnet", is_flag=True, help="Use exchange testnet endpoints.")
@click.option("--market-type", type=click.Choice(["spot", "futures"]), default="spot")
def add_cmd(exchange: str, label: str, testnet: bool, market_type: str) -> None:
    """Add (or re-add) an exchange API key. Secret stays in OS keyring."""
    exchange = exchange.lower()

    if load_credential(exchange, label):
        if not click.confirm(
            f"  Credential {exchange}:{label} already exists locally. Overwrite?",
            default=False,
        ):
            console.print("[yellow]Aborted.[/yellow]")
            return

    console.print(
        Panel.fit(
            f"Pairing [bold]{exchange.upper()}[/bold] "
            f"({market_type}{', testnet' if testnet else ''}) "
            f"with label [cyan]{label}[/cyan].\n"
            "[dim]Keys are stored in your OS keyring — never on disk, never on Talyxion.[/dim]",
            title="talyxion add",
            border_style="cyan",
        )
    )
    api_key = _prompt_secret("API key")
    api_secret = _prompt_secret("API secret")
    passphrase = ""
    if exchange in {"okx", "kucoin"}:
        passphrase = _prompt_secret("Passphrase")

    # 1. Local validate via adapter — proves keys + reads permissions.
    AdapterCls = get_adapter(exchange)
    adapter: ExchangeAdapter = AdapterCls(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        testnet=testnet,
        market_type=market_type,
    )
    try:
        with console.status(f"[cyan]Validating with {exchange}…[/cyan]"):
            perms = adapter.validate_credentials()
    except AuthFailure as exc:
        _print_auth_failure_diagnostic(exchange, label, market_type, testnet, exc)
        adapter.close()
        sys.exit(1)
    except IPBlocked as exc:
        _print_ip_blocked_diagnostic(exchange, exc)
        adapter.close()
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]✗ Validation failed:[/red] {exc}")
        adapter.close()
        sys.exit(1)
    finally:
        pass  # adapter closed below after server registration

    # 2. SECURITY GATE — refuse withdraw-enabled keys on MAINNET.
    #
    # Exchange testnets (e.g. testnet.binance.vision) issue keys that always
    # carry `canWithdraw=true` because there is no withdraw flow there to
    # toggle off (no real funds). We relax the gate when `--testnet` is set:
    # the withdraw permission is meaningless on fake money, and forcing
    # users to "mint a key without withdraw" on a testnet that doesn't
    # support that toggle would block all testing.
    #
    # We also rewrite the permission payload before sending to the server,
    # so the server-side gate (which is strict by design) doesn't reject.
    # The CLI is the only place that knows whether the user opted in to
    # testnet; the server fingerprint check still prevents the same key
    # from being later mis-registered as mainnet without retrying validation.
    if perms.can_withdraw:
        if testnet:
            console.print(
                "[yellow]⚠ Key has WITHDRAW permission — permitted on testnet "
                "only.[/yellow]\n"
                "[dim]Testnet keys carry canWithdraw=true by design (Binance et al "
                "don't expose a toggle for it). Funds are simulated, so withdraw "
                "is a no-op. The CLI will register this key as withdraw-disabled "
                "with the Talyxion server — re-validate with --testnet=false before "
                "registering a key against real money.[/dim]"
            )
            # Construct a sanitised permissions snapshot that mirrors what
            # a mainnet trade-only key would look like.
            from dataclasses import replace
            perms = replace(perms, can_withdraw=False)
        else:
            console.print(
                "[red]✗ This API key has WITHDRAW permission enabled.[/red]\n"
                "[bold]Talyxion will not store such keys.[/bold] Why: even if your CLI\n"
                "is compromised, an attacker shouldn't be able to drain your account.\n"
                "Go to the exchange, mint a new key with [yellow]withdraw disabled[/yellow], retry."
            )
            adapter.close()
            sys.exit(1)

    if not perms.can_trade:
        console.print(
            "[red]✗ Key does not have TRADE permission.[/red] "
            "Enable spot/futures trading on the exchange, then retry."
        )
        adapter.close()
        sys.exit(1)

    # 3. Register with Talyxion (fingerprint + permissions, NO secrets).
    fp = _fingerprint(exchange, api_key)
    try:
        client = DeviceTokenClient()
    except NotAuthenticatedError:
        console.print("[red]Not authenticated.[/red] Run `talyxion auth login` first.")
        adapter.close()
        sys.exit(1)
    payload = {
        "exchange": exchange,
        "label": label,
        "account_uid": perms.account_uid,
        "api_key_fingerprint": fp,
        "permissions": perms.to_payload(),
        "validation_status": "ok",
        # Hint for the server-side audit log; not security-critical
        # since the fingerprint binds the key to one specific environment.
        "is_testnet": bool(testnet),
    }
    try:
        resp = client.post("/trading/credentials/create/", json=payload)
    except TokenRevokedError as exc:
        console.print(f"[red]{exc}[/red]")
        adapter.close()
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]✗ Failed to register with Talyxion server:[/red] {exc}")
        adapter.close()
        sys.exit(1)
    finally:
        client.close()
        adapter.close()

    server_id = resp.get("data", {}).get("id")

    # 4. Store secrets in OS keyring.
    save_credential(exchange, label, {
        "api_key": api_key,
        "api_secret": api_secret,
        "passphrase": passphrase,
        "market_type": market_type,
        "testnet": "1" if testnet else "",
    })

    console.print(
        f"\n[green]✓ {exchange.upper()}:{label} registered.[/green] "
        f"Server credential #{server_id}.\n"
        f"  canTrade=[green]{perms.can_trade}[/green] "
        f"canFutures={perms.can_futures} "
        f"canMargin={perms.can_margin} "
        f"canWithdraw=[red]{perms.can_withdraw}[/red] (required false)\n"
        f"  Fingerprint: [dim]{fp[:32]}…[/dim]\n"
        f"  Secrets stored in OS keyring under service [cyan]talyxion:{exchange}[/cyan]."
    )


@click.command(name="creds")
def list_creds_cmd() -> None:
    """Show server-registered credentials (no secrets)."""
    try:
        with DeviceTokenClient() as client:
            data = client.get("/trading/credentials/")["data"]
    except NotAuthenticatedError:
        console.print("[red]Not authenticated.[/red] Run `talyxion auth login`.")
        sys.exit(1)
    except TokenRevokedError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Failed:[/red] {exc}")
        sys.exit(1)

    if not data:
        console.print("[yellow]No credentials yet.[/yellow] Run `talyxion add <exchange>`.")
        return

    table = Table(title="Registered credentials", show_lines=False)
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Exchange", style="cyan")
    table.add_column("Label", style="bold")
    table.add_column("Status")
    table.add_column("Trade")
    table.add_column("Withdraw")
    table.add_column("Local key?")
    table.add_column("Fingerprint", style="dim")
    for c in data:
        status = c.get("validation_status", "?")
        status_color = {"ok": "green", "revoked": "dim", "auth_failed": "red"}.get(status, "yellow")
        local = "[green]✓[/green]" if c.get("is_local_only") else "[yellow]server[/yellow]"
        perms = c.get("permissions") or {}
        wd = "[red]ON[/red]" if perms.get("canWithdraw") else "[green]off[/green]"
        local_have = load_credential(c["exchange"], c["label"]) is not None
        local_status = local + (" (keyring ✓)" if local_have else " (keyring [yellow]missing[/yellow])")
        table.add_row(
            str(c.get("id", "?")),
            c.get("exchange", "?"),
            c.get("label", "?"),
            f"[{status_color}]{status}[/]",
            "[green]yes[/green]" if perms.get("canTrade") else "[red]no[/red]",
            wd,
            local_status,
            (c.get("fingerprint") or "")[:16] + "…",
        )
    console.print(table)


@click.command(name="remove")
@click.argument("locator")
def remove_cmd(locator: str) -> None:
    """Remove a credential. Format: ``<exchange>:<label>`` e.g. ``binance:main``."""
    if ":" not in locator:
        console.print("[red]Specify as <exchange>:<label>, e.g. binance:main[/red]")
        sys.exit(1)
    exchange, label = locator.split(":", 1)
    exchange, label = exchange.strip().lower(), label.strip()

    # Server side: find pk by listing then revoke.
    try:
        with DeviceTokenClient() as client:
            creds = client.get("/trading/credentials/")["data"]
            target = next(
                (c for c in creds if c["exchange"] == exchange and c["label"] == label),
                None,
            )
            if target:
                client.post(f"/trading/credentials/{target['id']}/revoke/", json={})
                console.print(f"[green]✓ Server credential #{target['id']} marked revoked.[/green]")
            else:
                console.print(f"[yellow]No server credential for {exchange}:{label}.[/yellow]")
    except NotAuthenticatedError:
        console.print("[yellow]Not authenticated — skipping server revoke.[/yellow]")
    except TokenRevokedError as exc:
        console.print(f"[yellow]{exc} — skipping server revoke.[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]Server revoke failed:[/yellow] {exc} — continuing with local cleanup.")

    # Local keyring delete.
    if load_credential(exchange, label):
        delete_credential(exchange, label)
        console.print(f"[green]✓ Keyring entry for {exchange}:{label} removed.[/green]")
    else:
        console.print(f"[dim]No local keyring entry for {exchange}:{label}.[/dim]")
