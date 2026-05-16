"""Interactive REPL for ``talyxion``.

Run from the shell with no args (``talyxion``) — the entry in
:mod:`talyxion.cli.main` calls :func:`run_repl` when stdin/stdout are a
TTY. The REPL is the *only* user-facing surface; service managers use
:mod:`talyxion.cli.runner_entry` (``talyxion-runner``) instead.

Slash commands are dispatched via the existing Click handlers — we keep
Click as the parser and just translate ``/foo a b --c`` into argv. That
way every flag/option already exposed by `auth.py`, `credentials.py`,
etc. is automatically reachable inside the REPL with no extra wiring.
"""
from __future__ import annotations

import difflib
import os
import shlex
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import click
import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import (
    Completer,
    Completion,
    FuzzyCompleter,
    NestedCompleter,
)
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel

from talyxion.cli import auth as auth_mod
from talyxion.cli._version import __cli_version__
from talyxion.cli.auth import _AUTH_HEADERS, _save_and_announce
from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    NotAuthenticatedError,
    TokenRevokedError,
    _api_prefix,
    base_url,
)
from talyxion.cli.keyring_store import load_device_token, load_token_meta
from talyxion.cli.state import state_dir, state_path

console = Console()

# Registry of slash commands.
#   slug         → top-level slash name (without the leading "/")
#   argv         → tokens to pass into the internal Click root group
#   summary      → one-line description shown by /help
#   completions  → optional dict of sub-completions {next-token: dict-or-None}
#                  passed straight to NestedCompleter
@dataclass(frozen=True)
class SlashCommand:
    slug: str
    argv: tuple[str, ...]
    summary: str
    completions: dict[str, Any] | None = None


REGISTRY: tuple[SlashCommand, ...] = (
    # auth
    SlashCommand("login",   ("auth", "login"),  "Pair this device via OAuth device flow",
                 {"--label": None, "--open-browser": None}),
    SlashCommand("logout",  ("auth", "logout"), "Revoke the current device token", None),
    SlashCommand("status",  ("auth", "status"), "Show who you're authenticated as", None),
    # meta
    SlashCommand("whoami",  ("whoami",),  "Account + device-token info", None),
    SlashCommand("tier",    ("tier",),    "Subscription tier + quota usage", None),
    SlashCommand("doctor",  ("doctor",),  "Self-check (auth, server, keychain)", None),
    SlashCommand("update",  ("update",),  "Check for a newer talyxion release", None),
    # credentials
    SlashCommand("add",     ("add",),     "Register an exchange API key",
                 {"binance": {"--label": None, "--testnet": None,
                              "--market-type": {"spot": None, "futures": None}}}),
    SlashCommand("remove",  ("remove",),  "Delete a stored credential", None),
    SlashCommand("creds",   ("list", "creds"), "List server-side credentials", None),
    # profiles
    SlashCommand("profiles", ("list", "profiles"), "List trading profiles",
                 {"--scope": {"local": None, "all": None, "archived": None}}),
    SlashCommand("show",    ("show",),    "Drill into one profile by id/name", None),
    # money views
    SlashCommand("positions", ("positions",), "Open positions across profiles", None),
    SlashCommand("portfolio", ("portfolio",), "Aggregate wallet + P&L rollup",
                 {"--by": {"exchange": None, "profile": None, "mode": None, "symbol": None},
                  "--json": None}),
    SlashCommand("balance", ("balance",), "Wallet snapshot per credential", None),
    # live TUI
    SlashCommand("dashboard", ("dashboard",), "htop-style live portfolio TUI", None),
    # manual orders (bypass cycle dispatcher)
    SlashCommand("order", ("order",), "Place, cancel, list manual orders",
                 {"place": None, "cancel": None, "list": None, "cancel-all": None}),
    SlashCommand("tape", ("tape",), "Live tape of filled orders",
                 {"--profile": None, "--symbol": None, "--follow": None, "-f": None}),
    # daemon
    SlashCommand("run",      ("run",),    "Start the trading cycle loop",
                 {"--once": None, "-d": None, "--background": None, "--dry-run": None}),
    SlashCommand("runstate", ("status",), "Local runner state (peak equity, next due)", None),
    SlashCommand("logs",     ("logs",),   "Tail the rolling CLI log",
                 {"-n": None, "-f": None}),
)

REGISTRY_BY_SLUG: dict[str, SlashCommand] = {c.slug: c for c in REGISTRY}

# Bare-word aliases the user might type without a leading slash. Used by
# :func:`suggest_command` so ``bal`` still suggests ``/balance``, etc.
_META_WORDS = ("help", "exit", "quit", "clear")


def suggest_command(token: str, *, n: int = 3, cutoff: float = 0.55) -> list[str]:
    """Return up to ``n`` slash commands that look like ``token``.

    Used when the REPL sees a line that doesn't match any registered
    slash command. ``token`` may include or omit the leading ``/`` —
    we normalise it before matching. The returned list is already
    formatted with the leading slash so callers can print directly.
    """
    raw = (token or "").strip().lstrip("/").split()
    if not raw:
        return []
    first = raw[0].lower()
    pool = [c.slug for c in REGISTRY] + list(_META_WORDS)
    matches = difflib.get_close_matches(first, pool, n=n, cutoff=cutoff)
    # Substring fallback — difflib misses short prefixes like ``bal`` for
    # ``balance``. Append any pool entry that the token is a prefix of,
    # capped at ``n`` total.
    if len(matches) < n:
        for slug in pool:
            if slug.startswith(first) and slug not in matches:
                matches.append(slug)
                if len(matches) >= n:
                    break
    return [f"/{m}" for m in matches]


# ---------------------------------------------------------------------------
# Bootstrap: device-code flow when keyring is empty
# ---------------------------------------------------------------------------


def _device_flow_login() -> bool:
    """Run the OAuth device flow inline; return True on success.

    Mirrors :func:`talyxion.cli.auth.auth_login` but does not call
    ``sys.exit`` — the REPL stays alive on cancellation. On success the
    token is in the OS keyring; on failure we return False and let the
    caller decide whether to retry.
    """
    import platform
    import socket

    hostname = socket.gethostname()
    plat = f"{platform.system().lower()}-{platform.machine().lower()}"
    client_label = f"cli@{hostname}"

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
        return False

    user_code = start["user_code"]
    device_code = start["device_code"]
    verification_uri_complete = start.get("verification_uri_complete") or start["verification_uri"]
    interval = int(start.get("interval", 5))
    expires_in = int(start.get("expires_in", 600))

    # URL is the primary affordance. Rich's ``[link=...]`` markup makes
    # most modern terminals (iTerm2, Terminal.app, Kitty, WezTerm,
    # Alacritty, VSCode) render it as a Cmd/Ctrl-clickable hyperlink via
    # OSC 8. We do NOT auto-open the browser anymore — that surprised
    # users on headless machines and SSH sessions.
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
                _save_and_announce(
                    token,
                    body.get("token_prefix", ""),
                    body.get("label", client_label),
                )
                return True
            err = body.get("error", "")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = max(interval, interval + 2)
                continue
            if err == "access_denied":
                console.print("[red]Request denied in browser.[/red]")
                return False
            if err == "expired_token":
                console.print("[red]Code expired. Try /login again.[/red]")
                return False
            console.print(f"[red]Unexpected response:[/red] {body}")
            return False

    console.print("[red]Timed out waiting for approval.[/red]")
    return False


def bootstrap() -> bool:
    """Ensure we have a device token before dropping the user at the prompt.

    Returns True if authenticated (either already, or after a successful
    flow). False if the user cancels — the caller still enters the REPL
    so they can manually `/login` later.
    """
    if load_device_token():
        return True
    console.print(
        Panel.fit(
            "[bold]Welcome to Talyxion.[/bold]\n\n"
            "Looks like this is your first run. Let's pair this machine\n"
            "with your Talyxion account — your browser will open in a moment.",
            border_style="cyan",
            title="First-run pairing",
        )
    )
    return _device_flow_login()


# ---------------------------------------------------------------------------
# Slash dispatch — translate "/foo a --b" into Click argv
# ---------------------------------------------------------------------------


class SlashDispatcher:
    """Parse a REPL line and route to the internal Click group.

    The internal Click group is imported lazily so importing
    :mod:`talyxion.cli.repl` from tests does not eagerly pull in every
    subcommand module.
    """

    def __init__(self) -> None:
        from talyxion.cli.main import _internal_cli
        self._cli = _internal_cli

    def parse(self, line: str) -> list[str] | None:
        """Return Click argv for ``line``, or None if the line is a meta
        (handled outside Click) or empty.

        Lines starting with ``/`` resolve via the registry. Bare words
        like ``help`` / ``exit`` / ``quit`` / ``?`` are also accepted —
        they map to meta commands handled by the REPL loop.
        """
        s = line.strip()
        if not s:
            return None
        # Bare-word meta aliases (handled by caller).
        if s in {"exit", "quit", "help", "?", "clear"}:
            return ["__meta__", s]
        if not s.startswith("/"):
            return ["__unknown__", s]
        try:
            tokens = shlex.split(s[1:])
        except ValueError as exc:
            console.print(f"[red]Parse error:[/red] {exc}")
            return None
        if not tokens:
            return None
        slug, rest = tokens[0], tokens[1:]
        # Meta slashes
        if slug in {"exit", "quit", "help", "?", "clear", "h"}:
            return ["__meta__", slug, *rest]
        cmd = REGISTRY_BY_SLUG.get(slug)
        if not cmd:
            return ["__unknown__", slug]
        return [*cmd.argv, *rest]

    def dispatch(self, argv: list[str]) -> None:
        """Invoke the internal Click group with ``argv``, swallowing the
        SystemExit / UsageError variants so the REPL stays alive."""
        # Tell auth handlers that they're being invoked from the REPL —
        # they should not call sys.exit on "already authenticated" etc.
        auth_mod._INSIDE_REPL = True
        try:
            self._cli.main(args=argv, standalone_mode=False, prog_name="talyxion")
        except click.exceptions.UsageError as exc:
            console.print(f"[red]Usage:[/red] {exc.format_message()}")
        except click.exceptions.Abort:
            console.print("[yellow]Aborted.[/yellow]")
        except SystemExit as exc:
            # Click subcommands occasionally raise SystemExit (e.g. auth
            # errors). Stay in REPL — surface the code for debugging.
            if exc.code not in (0, None):
                console.print(f"[dim]command exited with code {exc.code}[/dim]")
        except KeyboardInterrupt:
            console.print("[yellow]Interrupted.[/yellow]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Error:[/red] {exc}")
        finally:
            auth_mod._INSIDE_REPL = False


# ---------------------------------------------------------------------------
# Completer
# ---------------------------------------------------------------------------


class _SlashCompleter(Completer):
    """First-token completer that renders slash commands with descriptions.

    Behaviour mirrors Claude Code's slash menu: as you type at the start
    of a line, a dropdown shows every command whose name contains the
    typed substring, with its one-line description on the right. Once
    you've committed the slash (typed a space after ``/foo``), control
    hands off to the flag completer for that command.
    """

    def __init__(self, fallback: NestedCompleter) -> None:
        self._fallback = fallback

    def get_completions(self, document, complete_event):
        text_before = document.text_before_cursor
        # Hand off to the nested (flag) completer once the user has moved
        # past the first token — i.e. there's whitespace anywhere before
        # the cursor. The nested completer already knows the flag tree
        # per command.
        stripped = text_before.lstrip()
        if " " in stripped:
            yield from self._fallback.get_completions(document, complete_event)
            return

        # First token: emit our annotated slash menu. Match substring so
        # typing ``bal`` finds ``/balance``, ``port`` finds ``/portfolio``.
        query = stripped.lstrip("/").lower()
        for cmd in REGISTRY:
            if query and query not in cmd.slug.lower():
                continue
            yield Completion(
                text=f"/{cmd.slug}",
                start_position=-len(text_before),
                display=f"/{cmd.slug}",
                display_meta=cmd.summary,
            )
        # Meta commands appear at the bottom of the menu.
        for meta_slug, meta_help in (
            ("help", "List slash commands"),
            ("clear", "Clear the screen"),
            ("exit", "Quit the REPL"),
            ("quit", "Quit the REPL"),
        ):
            if query and query not in meta_slug:
                continue
            yield Completion(
                text=f"/{meta_slug}",
                start_position=-len(text_before),
                display=f"/{meta_slug}",
                display_meta=meta_help,
            )


def build_completer() -> Completer:
    """Completer used by the REPL.

    Two layers:
      1. :class:`_SlashCompleter` provides a Claude-Code-style dropdown
         showing each command and its summary as the user types.
      2. :class:`FuzzyCompleter` wraps it so typos like ``/balanc`` or
         transposed letters still match. Fuzzy match runs against
         whatever the first layer yields, so flag completions inside a
         command stay exact (no fuzziness on ``--scope``).
    """
    # Nested fallback handles flag-level completion once the user is
    # inside a known command (``/add binance --testnet`` etc.).
    nested_tree: dict[str, Any] = {}
    for cmd in REGISTRY:
        nested_tree[f"/{cmd.slug}"] = cmd.completions
    nested_tree["/help"] = {f"/{c.slug}": None for c in REGISTRY}
    nested_fallback = NestedCompleter.from_nested_dict(nested_tree)

    return FuzzyCompleter(_SlashCompleter(nested_fallback))


# ---------------------------------------------------------------------------
# Bottom toolbar — refreshed in a background thread
# ---------------------------------------------------------------------------


@dataclass
class ToolbarState:
    tier: str = "?"
    email: str = "?"
    prefix: str = "?"
    daemon_pid: int | None = None
    version: str = __cli_version__
    lock: threading.Lock = field(default_factory=threading.Lock)

    def render(self) -> str:
        with self.lock:
            tier = self.tier.upper() if self.tier else "?"
            email = self.email or "?"
            prefix = self.prefix or "?"
            daemon = f"daemon=#{self.daemon_pid}" if self.daemon_pid else "daemon=off"
            return (
                f" {email}  ·  tier={tier}  ·  token={prefix}  "
                f"·  {daemon}  ·  v{self.version}  ·  /help for commands "
            )


def _refresh_toolbar(state: ToolbarState, stop_event: threading.Event) -> None:
    """Background thread: poll daemon pid every 3 s, whoami every 60 s."""
    from talyxion.cli.state import is_pid_alive
    last_whoami = 0.0
    while not stop_event.is_set():
        # daemon pid is cheap — file check only. Use the portable
        # ``is_pid_alive`` helper because ``os.kill(pid, 0)`` would
        # actually kill the process on Windows.
        pid_file = state_path().parent / "run.pid"
        pid: int | None = None
        if pid_file.exists():
            try:
                pid_candidate = int(pid_file.read_text().strip())
            except (OSError, ValueError):
                pid_candidate = 0
            if pid_candidate and is_pid_alive(pid_candidate):
                pid = pid_candidate
        with state.lock:
            state.daemon_pid = pid

        # whoami is a network call — do it sparingly.
        now = time.time()
        if now - last_whoami > 60 and load_device_token():
            last_whoami = now
            try:
                with DeviceTokenClient() as client:
                    who = client.get("/trading/whoami/")["data"]
                with state.lock:
                    state.tier = who.get("tier", "?")
                    state.email = who.get("email", "?")
                    token = who.get("token", {})
                    state.prefix = token.get("prefix", "?")
            except (NotAuthenticatedError, TokenRevokedError, Exception):
                pass
        stop_event.wait(3.0)


# ---------------------------------------------------------------------------
# Meta commands handled inside the REPL (not via Click)
# ---------------------------------------------------------------------------


def _print_help(arg: str | None = None) -> None:
    if arg:
        slug = arg.lstrip("/")
        cmd = REGISTRY_BY_SLUG.get(slug)
        if not cmd:
            console.print(f"[red]Unknown command:[/red] /{slug}")
            return
        # Defer to Click's own --help so flags stay in lockstep.
        from talyxion.cli.main import _internal_cli
        try:
            _internal_cli.main(
                args=[*cmd.argv, "--help"],
                standalone_mode=False,
                prog_name="talyxion",
            )
        except SystemExit:
            pass
        return

    # Grouped overview.
    groups = [
        ("Auth",          ["login", "logout", "status"]),
        ("Account",       ["whoami", "tier", "doctor", "update"]),
        ("Credentials",   ["add", "remove", "creds"]),
        ("Profiles",      ["profiles", "show"]),
        ("Live view",     ["dashboard"]),
        ("Manual trading", ["order", "tape"]),
        ("Money views",   ["positions", "portfolio", "balance"]),
        ("Daemon",        ["run", "runstate", "logs"]),
    ]
    lines: list[str] = []
    for title, slugs in groups:
        lines.append(f"[bold cyan]{title}[/bold cyan]")
        for slug in slugs:
            cmd = REGISTRY_BY_SLUG.get(slug)
            if not cmd:
                continue
            lines.append(f"  [bold]/{cmd.slug:<10}[/bold] — {cmd.summary}")
        lines.append("")
    lines.append(
        "[dim]Meta: /clear, /exit (or exit / Ctrl-D). /help <cmd> for flags.[/dim]"
    )
    console.print(Panel("\n".join(lines).rstrip(),
                        title="Slash commands", border_style="cyan"))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


_PROMPT_STYLE = Style.from_dict({
    "prompt": "#88e0ff bold",
    "bottom-toolbar": "bg:#1c2233 #cdd5e0",
})


def _banner() -> None:
    meta = load_token_meta() or {}
    line = "Talyxion thin trader"
    sub = f"v{__cli_version__}  ·  {base_url()}"
    if meta.get("email"):
        sub += f"  ·  signed in as {meta['email']} ({meta.get('tier','?').upper()})"
    console.print(
        Panel.fit(
            f"[bold cyan]{line}[/bold cyan]\n[dim]{sub}[/dim]\n\n"
            "Type [bold]/help[/bold] to list slash commands.\n"
            "Type [bold]exit[/bold] or hit Ctrl-D to quit.",
            border_style="cyan",
        )
    )


def run_repl() -> int:
    """Entry point invoked by ``talyxion`` (no args, in a TTY)."""
    _banner()
    if not bootstrap():
        console.print(
            "[yellow]You're not signed in yet — type[/yellow] [bold]/login[/bold] "
            "[yellow]when you're ready.[/yellow]"
        )

    history_file = state_dir() / "repl_history"
    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_file)),
        completer=build_completer(),
        # Show the slash-command dropdown the moment the user starts
        # typing — matches Claude Code's UX. Tab still works for
        # commit / cycle.
        complete_while_typing=True,
        auto_suggest=AutoSuggestFromHistory(),
        bottom_toolbar=lambda: toolbar_state.render(),
        style=_PROMPT_STYLE,
    )
    dispatcher = SlashDispatcher()

    toolbar_state = ToolbarState()
    stop_event = threading.Event()
    refresh_thread = threading.Thread(
        target=_refresh_toolbar,
        args=(toolbar_state, stop_event),
        daemon=True,
        name="talyxion-toolbar",
    )
    refresh_thread.start()

    try:
        while True:
            try:
                line = session.prompt("› ")
            except KeyboardInterrupt:
                # Ctrl-C clears the current line but stays in REPL.
                continue
            except EOFError:
                # Ctrl-D exits.
                console.print("\n[dim]bye.[/dim]")
                return 0

            argv = dispatcher.parse(line)
            if argv is None:
                continue
            if argv[0] == "__meta__":
                meta_cmd = argv[1]
                if meta_cmd in {"exit", "quit"}:
                    console.print("[dim]bye.[/dim]")
                    return 0
                if meta_cmd in {"help", "?", "h"}:
                    _print_help(argv[2] if len(argv) > 2 else None)
                    continue
                if meta_cmd == "clear":
                    console.clear()
                    continue
            if argv[0] == "__unknown__":
                token = argv[1]
                suggestions = suggest_command(token)
                msg = f"[red]Unknown command:[/red] {token}"
                if suggestions:
                    pretty = "  ".join(f"[cyan]{s}[/cyan]" for s in suggestions)
                    msg += f"\n[dim]Did you mean:[/dim] {pretty}"
                else:
                    msg += "  [dim](try /help)[/dim]"
                console.print(msg)
                continue
            dispatcher.dispatch(argv)
    finally:
        stop_event.set()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_repl())
