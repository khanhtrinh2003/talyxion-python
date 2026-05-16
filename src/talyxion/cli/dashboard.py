"""``/dashboard`` — htop-style live portfolio TUI.

Single full-screen view that aggregates everything a working trader
wants to see at once: profile status, open positions, recent fills,
daemon heartbeat. Refresh runs in a background thread; the main thread
reads stdin one character at a time for keybinds (``q`` to quit, ``r``
to force-refresh, ``p`` to pause every active profile, ``a`` to toggle
archived rows). Terminal state is restored in ``finally`` so a crash
mid-loop doesn't leave the user with a broken shell.

Polling cadence is 2 s for now; Phase 2.3 will swap the poll thread
for a WebSocket subscription (``/ws/positions/<id>/``) and the keybind
loop stays unchanged. Until then we re-use the existing REST endpoints
the web dashboard already calls:

* ``GET /trading/profiles/?include=all`` for the profile rows
* ``GET /trading/profiles/<pk>/positions/api/`` per active profile
* ``GET /trading/profiles/<pk>/orders/?since=<iso>&limit=10`` for the
  rolling fills tape (uses the new ``since=`` filter added to
  :func:`main.api.v1.trading_views.profile_orders`).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque

# Dashboard reads stdin one character at a time without blocking the
# refresh thread. POSIX needs cbreak mode + select() on the file
# descriptor; Windows needs msvcrt.kbhit()/getch(). We pick the backend
# at import time and expose a uniform ``_RawTerminal`` + ``_read_key_nowait``
# pair so the main loop below stays platform-agnostic.
_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:
    import msvcrt  # type: ignore[import-not-found]
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]
    select_mod = None
    _HAVE_KEYBOARD = True
else:
    try:
        import select as select_mod
        import termios
        import tty
        _HAVE_KEYBOARD = True
    except ImportError:
        termios = None  # type: ignore[assignment]
        tty = None  # type: ignore[assignment]
        select_mod = None
        _HAVE_KEYBOARD = False

# Backwards-compat alias retained for the existing test suite, which
# asserts ``_HAVE_TERMIOS`` exists. Maps directly to the unified
# keyboard-available flag.
_HAVE_TERMIOS = _HAVE_KEYBOARD
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import click
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from talyxion.cli._version import __cli_version__
from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    FriendlyHTTPError,
    NotAuthenticatedError,
    TokenRevokedError,
    explain_http_failure,
)
from talyxion.cli.keyring_store import load_token_meta
from talyxion.cli.state import is_pid_alive, state_path

console = Console()

REFRESH_INTERVAL_S = 2.0
TAPE_MAX_ROWS = 12
DEFAULT_HEADLINE_LIMIT = 8  # profile rows shown without scrolling


# ---------------------------------------------------------------------------
# Shared state between refresh thread and render
# ---------------------------------------------------------------------------


class DashboardState:
    """Thread-safe snapshot of everything the layout needs to render.

    The refresh thread mutates these fields under ``lock``; the render
    callback reads them under the same lock to build the Layout. Using
    a dataclass would be cleaner but we want the lock around the whole
    snapshot, not per-field.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.profiles: list[dict[str, Any]] = []
        self.positions_by_profile: dict[int, list[dict[str, Any]]] = {}
        self.tape: deque[dict[str, Any]] = deque(maxlen=TAPE_MAX_ROWS)
        self.tape_since: str | None = None
        self.last_refresh: datetime | None = None
        self.last_error: str | None = None
        self.show_archived = False
        self.notice: str | None = None  # transient one-shot message (e.g. "paused all")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "profiles": list(self.profiles),
                "positions_by_profile": dict(self.positions_by_profile),
                "tape": list(self.tape),
                "last_refresh": self.last_refresh,
                "last_error": self.last_error,
                "show_archived": self.show_archived,
                "notice": self.notice,
            }


# ---------------------------------------------------------------------------
# Refresh thread — polls the server, fills DashboardState
# ---------------------------------------------------------------------------


def _refresh_once(state: DashboardState, force: bool = False) -> None:
    """One pass of REST polling: profiles + positions + fills tape.

    Positions come from the ``last_app_positions`` field of each profile
    (i.e. the most recent heartbeat snapshot, already returned by
    ``/trading/profiles/``). We deliberately don't call the web
    ``positions/api/`` endpoint — it lives outside ``/api/v1/talyxion/``
    and uses session auth, so a device-token CLI gets 404 from it.
    Heartbeat snapshots are 1–60 s fresh which is plenty for a TUI.
    """
    try:
        with DeviceTokenClient() as client:
            include = "archived" if state.show_archived else "all"
            payload = client.get(f"/trading/profiles/?include={include}")
            profiles = payload.get("data") or []

            positions_by_profile: dict[int, list[dict[str, Any]]] = {}
            for p in profiles:
                if p.get("status") == "archived":
                    continue
                raw_positions = p.get("last_app_positions") or []
                # Heartbeat shape (see main/api/v1/trading_views.py:644-655):
                # symbol, qty, entry_price, mark_price, signed_notional_usd,
                # unrealized_pnl_usd, unrealized_pnl_pct. Decimal-valued
                # fields arrive as strings via the JSONField — the layout
                # uses ``_as_float`` to coerce on demand. Derive ``side``
                # from sign(qty) since the heartbeat doesn't store it.
                norm: list[dict[str, Any]] = []
                for pos in raw_positions:
                    if not isinstance(pos, dict):
                        continue
                    qty_f = _as_float(pos.get("qty"))
                    side = "long" if qty_f >= 0 else "short"
                    norm.append({
                        "symbol": pos.get("symbol", ""),
                        "side": side,
                        "qty": abs(qty_f) if qty_f else pos.get("qty"),
                        "entry_price": pos.get("entry_price"),
                        "mark_price": pos.get("mark_price"),
                        "unrealized_pnl": pos.get("unrealized_pnl_usd"),
                        "pnl_pct": pos.get("unrealized_pnl_pct"),
                    })
                if norm:
                    positions_by_profile[p["id"]] = norm

            # Fills tape — request only events newer than last seen.
            new_fills: list[dict[str, Any]] = []
            for p in profiles:
                if p.get("status") == "archived":
                    continue
                params = "?limit=10"
                if state.tape_since:
                    from urllib.parse import quote as _quote
                    params += f"&since={_quote(state.tape_since)}"
                try:
                    orders_payload = client.get(
                        f"/trading/profiles/{p['id']}/orders/{params}"
                    )
                except Exception:  # noqa: BLE001
                    continue
                for row in orders_payload.get("data") or []:
                    row["_profile_name"] = p.get("name", "?")
                    new_fills.append(row)
    except (NotAuthenticatedError, TokenRevokedError) as exc:
        with state.lock:
            state.last_error = str(exc)
        return
    except Exception as exc:  # noqa: BLE001
        headline, _ = explain_http_failure(exc, "/trading/profiles/")
        with state.lock:
            state.last_error = headline
        return

    # Sort fills newest-first; we'll add to a deque that already keeps
    # them in that order (insert at the left).
    new_fills.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    now = datetime.now(timezone.utc)
    with state.lock:
        state.profiles = profiles
        state.positions_by_profile = positions_by_profile
        for row in reversed(new_fills):  # so newest ends up on top of deque
            state.tape.appendleft(row)
        if new_fills:
            # Track the latest seen timestamp for the next ``since=`` query.
            latest = max((r.get("created_at") or "") for r in new_fills)
            if latest:
                state.tape_since = latest
        state.last_refresh = now
        state.last_error = None


def _refresh_loop(state: DashboardState, stop: threading.Event,
                  wake: threading.Event) -> None:
    while not stop.is_set():
        _refresh_once(state)
        # Wait up to REFRESH_INTERVAL_S, but wake immediately if the
        # main thread asks for a force-refresh.
        wake.wait(REFRESH_INTERVAL_S)
        wake.clear()


# ---------------------------------------------------------------------------
# Layout building — pure functions of DashboardState snapshot
# ---------------------------------------------------------------------------


def _as_float(v: Any, default: float = 0.0) -> float:
    """Coerce to float; tolerates None, str (incl. Decimal-stringified),
    and numeric types. Server endpoints sometimes return ``"7.74"`` for
    monetary fields (Decimal serialisation), so a naive ``v >= 0`` would
    crash. Use this anywhere numeric comparison or % formatting runs."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt_usd(v: Any, signed: bool = False) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if (signed and n >= 0) else ""
    return f"{sign}${n:,.2f}"


def _fmt_qty(v: Any) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    s = f"{n:,.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _ago(ts: datetime | None) -> str:
    if ts is None:
        return "never"
    delta = (datetime.now(timezone.utc) - ts).total_seconds()
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    return f"{int(delta / 3600)}h ago"


def _build_header(snap: dict[str, Any]) -> Panel:
    meta = load_token_meta() or {}
    pid_file = state_path().parent / "run.pid"
    daemon = "off"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            pid = 0
        if pid and is_pid_alive(pid):
            daemon = f"#{pid}"
        elif pid:
            daemon = "stale-pid"
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    left = (
        f"[bold cyan]talyxion[/bold cyan] "
        f"· {meta.get('email','?')} "
        f"· tier=[magenta]{(meta.get('tier') or '?').upper()}[/] "
        f"· daemon={daemon} · v{__cli_version__}"
    )
    refreshed = _ago(snap["last_refresh"])
    err = snap.get("last_error")
    right = (
        f"[red]✗ {err}[/red]" if err
        else f"[dim]updated {refreshed} · {now}Z[/dim]"
    )
    notice = snap.get("notice")
    if notice:
        right = f"[yellow]{notice}[/yellow]  ·  " + right
    return Panel(
        f"{left}\n{right}",
        border_style="cyan",
        height=4,
    )


def _build_profiles_panel(snap: dict[str, Any]) -> Panel:
    profiles = snap["profiles"]
    if not snap["show_archived"]:
        profiles = [p for p in profiles if p.get("status") != "archived"]
    profiles = profiles[:DEFAULT_HEADLINE_LIMIT]
    tbl = Table(expand=True, show_lines=False, pad_edge=False)
    tbl.add_column("#", style="dim", width=4, justify="right")
    tbl.add_column("Name", overflow="fold")
    tbl.add_column("Ex", width=4, style="cyan")
    tbl.add_column("Mode", width=5)
    tbl.add_column("Status", width=8)
    tbl.add_column("Wallet", justify="right")
    tbl.add_column("uPnL", justify="right")
    status_color = {
        "active": "green", "paused": "yellow", "draft": "dim",
        "error": "red", "archived": "dim",
    }
    for p in profiles:
        st = p.get("status", "?")
        color = status_color.get(st, "white")
        wallet = p.get("last_app_wallet_usd")
        upnl = p.get("last_app_unrealized_pnl")
        # Server returns Decimal-as-string for monetary fields — coerce
        # before the >= 0 comparison so we don't TypeError.
        upnl_c = "green" if _as_float(upnl) >= 0 else "red"
        row_style = "dim" if st in ("archived", "paused") else None
        tbl.add_row(
            str(p.get("id", "?")),
            p.get("name", "?"),
            p.get("exchange", "?")[:3],
            p.get("mode", "?")[:4],
            f"[{color}]{st}[/]",
            _fmt_usd(wallet),
            f"[{upnl_c}]{_fmt_usd(upnl, signed=True)}[/]"
            if upnl is not None else "—",
            style=row_style,
        )
    title = f"Profiles ({len(snap['profiles'])})"
    if snap["show_archived"]:
        title += " · incl. archived"
    return Panel(tbl, title=title, border_style="blue")


def _build_positions_panel(snap: dict[str, Any]) -> Panel:
    rows = []
    for pid, positions in snap["positions_by_profile"].items():
        for pos in positions:
            rows.append((pid, pos))
    tbl = Table(expand=True, show_lines=False, pad_edge=False)
    tbl.add_column("Symbol", style="bold")
    tbl.add_column("Side", width=5)
    tbl.add_column("Qty", justify="right")
    tbl.add_column("Entry", justify="right", style="dim")
    tbl.add_column("Mark", justify="right")
    tbl.add_column("uPnL", justify="right")
    tbl.add_column("%", justify="right", style="dim")
    if not rows:
        tbl.add_row("[dim]—[/dim]", "", "", "", "", "", "")
    for _pid, pos in rows[:20]:
        side = (pos.get("side") or "").lower()
        side_c = "green" if side == "long" or side == "buy" else "red"
        upnl = pos.get("unrealized_pnl")
        upnl_f = _as_float(upnl)
        upnl_c = "green" if upnl_f >= 0 else "red"
        pnl_pct_f = _as_float(pos.get("pnl_pct"))
        tbl.add_row(
            pos.get("symbol", "?"),
            f"[{side_c}]{side}[/]",
            _fmt_qty(pos.get("qty")),
            _fmt_qty(pos.get("entry_price")),
            _fmt_qty(pos.get("mark_price")),
            f"[{upnl_c}]{_fmt_usd(upnl, signed=True)}[/]",
            f"[{upnl_c}]{pnl_pct_f:+.2f}%[/]",
        )
    return Panel(tbl, title=f"Positions ({len(rows)} open)", border_style="magenta")


def _build_tape_panel(snap: dict[str, Any]) -> Panel:
    rows = snap["tape"]
    tbl = Table(expand=True, show_lines=False, pad_edge=False, box=None)
    tbl.add_column("Time", width=8, style="dim")
    tbl.add_column("Profile", width=18, style="cyan")
    tbl.add_column("Side", width=5)
    tbl.add_column("Symbol", style="bold")
    tbl.add_column("Notional", justify="right")
    tbl.add_column("Status")
    if not rows:
        tbl.add_row("[dim]waiting for fills…[/dim]", "", "", "", "", "")
    for row in rows:
        ts_raw = row.get("created_at") or ""
        ts = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
        side = (row.get("side") or "").lower()
        side_c = "green" if side in ("long", "buy") else "red"
        st = row.get("status", "?")
        st_c = {"filled": "green", "partial": "yellow",
                "rejected": "red", "submitted": "cyan"}.get(st, "white")
        tbl.add_row(
            ts,
            row.get("_profile_name", "?"),
            f"[{side_c}]{side}[/]",
            row.get("symbol", "?"),
            _fmt_usd(row.get("usd_amount")),
            f"[{st_c}]{st}[/]",
        )
    return Panel(tbl, title="Recent fills (tape)", border_style="yellow")


def _build_footer() -> Panel:
    return Panel(
        "[bold]q[/bold] quit  ·  [bold]r[/bold] refresh now  ·  "
        "[bold]a[/bold] toggle archived  ·  "
        "[bold]/[/bold] back to REPL  ·  Ctrl-C exits",
        border_style="dim",
        height=3,
    )


def _build_layout(state: DashboardState) -> Layout:
    snap = state.snapshot()
    layout = Layout()
    layout.split_column(
        Layout(_build_header(snap), name="header", size=4),
        Layout(name="middle", ratio=2),
        Layout(_build_tape_panel(snap), name="tape", ratio=1),
        Layout(_build_footer(), name="footer", size=3),
    )
    layout["middle"].split_row(
        Layout(_build_profiles_panel(snap), name="left"),
        Layout(_build_positions_panel(snap), name="right"),
    )
    return layout


# ---------------------------------------------------------------------------
# Keyboard handling — read one char at a time without blocking refresh
# ---------------------------------------------------------------------------


class _RawTerminal:
    """Context manager: put stdin in char-at-a-time mode + restore on exit.

    Platform behaviour:
      * POSIX (macOS/Linux): puts the tty in cbreak via ``tty.setcbreak``,
        restores via ``termios.tcsetattr(..., TCSADRAIN, old)``.
      * Windows: no-op. ``msvcrt.getch`` reads one char at a time from
        the console directly without needing mode changes.
    """

    def __init__(self) -> None:
        # ``fileno`` raises in non-tty environments (e.g. captured stdin
        # under pytest); guard so the context manager works headlessly
        # for unit tests that don't actually read keys.
        self.fd = -1
        if not _IS_WINDOWS and _HAVE_KEYBOARD:
            try:
                self.fd = sys.stdin.fileno()
            except (OSError, ValueError):
                self.fd = -1
        self.old: list | None = None

    def __enter__(self) -> "_RawTerminal":
        if _IS_WINDOWS or not _HAVE_KEYBOARD:
            return self
        if not sys.stdin.isatty() or self.fd < 0:
            return self
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.old is not None and not _IS_WINDOWS and _HAVE_KEYBOARD:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def _read_key_nowait(timeout: float = 0.1) -> str | None:
    """Return one keystroke if available within ``timeout`` seconds.

    Windows uses ``msvcrt.kbhit()`` (non-blocking probe) + ``getwch``;
    POSIX uses ``select`` on the stdin fd. Both return ``None`` when
    nothing's pressed in time so the caller can re-paint and re-poll.
    """
    if not sys.stdin.isatty():
        return None

    if _IS_WINDOWS:
        # No native ``select(stdin)`` on Windows; busy-poll with a
        # short sleep until either a key shows up or the timeout
        # elapses. The granularity is 20 ms — same UX as POSIX.
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            if msvcrt.kbhit():
                try:
                    ch = msvcrt.getwch()
                except Exception:  # noqa: BLE001
                    return None
                # Function / arrow keys come through as two-char
                # sequences starting with \x00 or \xe0 — read and
                # drop the second byte so the caller never sees a
                # half-encoded key.
                if ch in ("\x00", "\xe0"):
                    try:
                        msvcrt.getwch()
                    except Exception:  # noqa: BLE001
                        pass
                    return None
                return ch
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)

    # POSIX path
    r, _, _ = select_mod.select([sys.stdin], [], [], timeout)
    if not r:
        return None
    try:
        return sys.stdin.read(1)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@click.command("dashboard")
def dashboard_cmd() -> None:
    """Open the htop-style live portfolio TUI.

    Refresh every ~2 seconds via REST polling (Phase 2.3 swaps this for
    a WebSocket subscription so updates land the moment a fill arrives).
    Press ``q`` to quit, ``r`` to force-refresh, ``p`` to pause every
    active profile, ``a`` to toggle archived rows. Ctrl-C also exits
    cleanly.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print("[red]/dashboard requires a TTY.[/red]")
        raise SystemExit(2)
    if not _HAVE_KEYBOARD:
        # All three supported platforms ship a keyboard backend; this
        # branch fires only on exotic targets (e.g. Jython / a stripped
        # Python build without termios on POSIX). Bail gracefully.
        console.print(
            "[red]/dashboard couldn't initialise the keyboard reader[/red] — "
            "neither msvcrt (Windows) nor termios (POSIX) is available. "
            "Use /profiles + /positions + /tape for now."
        )
        raise SystemExit(2)

    state = DashboardState()
    stop = threading.Event()
    wake = threading.Event()

    # Kick a synchronous fetch before entering Live so the first paint
    # has real data instead of empty placeholders.
    _refresh_once(state, force=True)

    refresh_thread = threading.Thread(
        target=_refresh_loop, args=(state, stop, wake), daemon=True,
        name="talyxion-dashboard-refresh",
    )
    refresh_thread.start()

    notice_clear_at: float | None = None
    try:
        with _RawTerminal():
            with Live(
                _build_layout(state),
                console=console,
                refresh_per_second=4,
                screen=True,
                transient=False,
            ) as live:
                while True:
                    key = _read_key_nowait(timeout=0.25)
                    if key:
                        if key == "q" or key == "Q":
                            break
                        if key == "\x03":  # Ctrl-C
                            break
                        if key == "r" or key == "R":
                            wake.set()
                        elif key == "a" or key == "A":
                            with state.lock:
                                state.show_archived = not state.show_archived
                            wake.set()
                        elif key == "/":
                            # Drop back to the REPL prompt; user can type
                            # one slash command then press Enter to return.
                            break
                    # Clear stale notice text after a few seconds.
                    if notice_clear_at and time.time() > notice_clear_at:
                        with state.lock:
                            state.notice = None
                        notice_clear_at = None
                    live.update(_build_layout(state))
    finally:
        stop.set()
        wake.set()
