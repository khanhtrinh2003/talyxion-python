"""``talyxion list profiles`` / ``positions`` — read-only views of server-side state."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from rich.panel import Panel

from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    FriendlyHTTPError,
    NotAuthenticatedError,
    TokenRevokedError,
    explain_http_failure,
)

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch(path: str) -> list[dict[str, Any]]:
    """Fetch a server resource, exit cleanly on auth failure.

    Same panel-and-exit envelope as :func:`talyxion.cli.portfolio._get`
    so /balance, /positions, /portfolio, /profiles, /show all give
    identical "what to try" guidance on 404 / 5xx / network errors.
    """
    try:
        with DeviceTokenClient() as client:
            payload = client.get(path)
    except NotAuthenticatedError:
        console.print("[red]Not authenticated.[/red] Type /login to pair this device.")
        raise SystemExit(1)
    except TokenRevokedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        headline, hint = explain_http_failure(exc, path)
        console.print(f"[red]✗ {headline}[/red]")
        if hint:
            console.print(Panel(hint, border_style="yellow", title="What to try"))
        raise SystemExit(1)
    return payload.get("data") or []


def _dec(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _fmt_usd(val: Any, places: int = 2) -> str:
    d = _dec(val)
    if d is None:
        return "—"
    return f"${d:,.{places}f}"


def _fmt_qty(val: Any) -> str:
    d = _dec(val)
    if d is None:
        return "—"
    # Trim trailing zeros for tidier display, but keep at least 4 dp for small qtys.
    s = f"{d:,.8f}".rstrip("0").rstrip(".")
    return s or "0"


def _status_color(status: str) -> str:
    return {
        "active":  "green",
        "paused":  "yellow",
        "error":   "red",
        "archived":"dim",
        "draft":   "dim",
    }.get(status, "white")


def _exec_label(mode: str) -> str:
    """Pretty execution-mode tag — colour-coded so server-side rows are
    visually obvious in a list dominated by CLI-local rows."""
    if mode == "local":
        return "[cyan]local[/cyan]"
    if mode == "server":
        return "[magenta]server[/magenta]"
    return mode or "?"


# ---------------------------------------------------------------------------
# ``talyxion list ...``
# ---------------------------------------------------------------------------


@click.group(name="list")
def list_group():
    """Browse server-side state (profiles, credentials, positions)."""


@list_group.command("profiles")
@click.option(
    "--scope",
    type=click.Choice(["local", "all", "archived"]),
    default="all",
    show_default=True,
    help=(
        "Which profiles to show. 'local' = only execution_mode=local (the "
        "ones this CLI manages). 'all' = every active profile incl. "
        "server-side. 'archived' = include archived too."
    ),
)
def list_profiles(scope: str):
    """List trading profiles owned by this account.

    By default shows ALL profiles so the CLI listing matches the web UI.
    Use ``--scope local`` to filter to only the rows this CLI manages.
    Server-side profiles are shown read-only (you can't ``run`` them from
    the CLI — they execute via the Celery dispatcher on Talyxion's IP).
    """
    data = _fetch(f"/trading/profiles/?include={scope}")
    if not data:
        if scope == "local":
            console.print("[yellow]No local-execution profiles yet.[/yellow]")
            console.print(
                "Create one at [link]https://talyxion.com/trading/profiles/new/[/link] "
                "and set execution_mode=local."
            )
        else:
            console.print("[yellow]No profiles yet.[/yellow]")
            console.print("Create one at [link]https://talyxion.com/trading/profiles/new/[/link].")
        return

    table = Table(title=f"Trading profiles ({scope})", show_lines=False)
    table.add_column("#",        style="dim",  width=4, justify="right")
    table.add_column("Name",     style="bold")
    table.add_column("Exchange", style="cyan")
    table.add_column("Exec")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Lev.",     justify="right")
    table.add_column("Cycle s",  justify="right", style="dim")
    table.add_column("Book",     justify="right", style="dim")
    table.add_column("Wallet",   justify="right")
    table.add_column("uPnL",     justify="right")
    table.add_column("Alpha",    style="dim")

    counts = {"local": 0, "server": 0}
    for p in data:
        exec_mode = p.get("execution_mode") or "?"
        counts[exec_mode] = counts.get(exec_mode, 0) + 1
        sim_or_live = p.get("mode", "?")
        sim_color = "yellow" if sim_or_live == "live" else "blue"
        st = p.get("status", "?")
        upnl = _dec(p.get("last_app_unrealized_pnl"))
        upnl_str = "—"
        if upnl is not None:
            upnl_color = "green" if upnl >= 0 else "red"
            upnl_str = f"[{upnl_color}]{upnl:+,.2f}[/]"

        table.add_row(
            str(p.get("id", "?")),
            p.get("name", "—"),
            f'{p.get("exchange","?")} · {p.get("market_type","")}',
            _exec_label(exec_mode),
            f'[{sim_color}]{sim_or_live}[/]',
            f'[{_status_color(st)}]{st}[/]',
            f'{p.get("order_leverage","?")}×',
            str(p.get("cycle_interval_sec", "?")),
            _fmt_usd(p.get("profile_book_usd"), 0),
            _fmt_usd(p.get("last_app_wallet_usd")),
            upnl_str,
            (p.get("alpha_id") or "—")[:12],
        )
    console.print(table)
    n_local = counts.get("local", 0)
    n_server = counts.get("server", 0)
    legend = []
    if n_local:
        legend.append(f"[cyan]{n_local} local[/cyan] (managed by this CLI)")
    if n_server:
        legend.append(f"[magenta]{n_server} server[/magenta] (read-only — runs on Talyxion)")
    if legend:
        console.print("  " + " · ".join(legend))


@list_group.command("positions")
@click.option(
    "--profile", "profile_filter",
    default=None,
    help="Filter to one profile by name or id (substring match on name).",
)
@click.option(
    "--scope",
    type=click.Choice(["local", "all"]),
    default="all",
    show_default=True,
    help="Which profiles' positions to include.",
)
def list_positions(profile_filter: str | None, scope: str):
    """Hiển thị danh mục: vị thế mở trên từng profile + tổng wallet/uPnL.

    Positions come from the most recent heartbeat each profile sent to
    Talyxion — i.e. as fresh as your `talyxion run` daemon is for local
    profiles, or as fresh as the server dispatcher's last cycle for
    server-side profiles. No exchange API calls are made by this command.
    """
    data = _fetch(f"/trading/profiles/?include={scope}")
    if not data:
        console.print("[yellow]No profiles available.[/yellow]")
        return

    # Optional filter
    if profile_filter:
        needle = profile_filter.strip().lower()
        filtered = [
            p for p in data
            if needle == str(p.get("id", "")).lower()
               or needle in (p.get("name") or "").lower()
        ]
        if not filtered:
            console.print(f"[yellow]No profile matching '{profile_filter}'.[/yellow]")
            return
        data = filtered

    # Totals
    total_wallet = Decimal("0")
    total_upnl   = Decimal("0")
    total_notional = Decimal("0")
    n_open = 0

    for p in data:
        positions = p.get("last_app_positions") or []
        wallet = _dec(p.get("last_app_wallet_usd")) or Decimal("0")
        upnl   = _dec(p.get("last_app_unrealized_pnl")) or Decimal("0")
        exec_mode = p.get("execution_mode") or "?"

        title = (
            f'[bold]{p.get("name","?")}[/bold]  '
            f'[dim]#{p.get("id","?")}[/dim]  '
            f'· {p.get("exchange","?")}/{p.get("market_type","?")}  '
            f'· {_exec_label(exec_mode)}  '
            f'· wallet {_fmt_usd(wallet)}  '
            f'· uPnL ' + (
                f'[green]+{upnl:,.2f}[/green]' if upnl >= 0
                else f'[red]{upnl:,.2f}[/red]'
            )
        )

        if not positions:
            console.print(title)
            console.print("  [dim]no open positions[/dim]\n")
            total_wallet += wallet
            total_upnl   += upnl
            continue

        tbl = Table(show_header=True, header_style="dim", show_lines=False, expand=False)
        tbl.add_column("Symbol", style="bold")
        tbl.add_column("Side")
        tbl.add_column("Qty",      justify="right")
        tbl.add_column("Entry",    justify="right", style="dim")
        tbl.add_column("Mark",     justify="right", style="dim")
        tbl.add_column("Notional", justify="right")
        tbl.add_column("uPnL",     justify="right")

        for pos in positions:
            qty   = _dec(pos.get("qty") or pos.get("amount") or pos.get("size"))
            entry = _dec(pos.get("entry") or pos.get("entry_price"))
            mark  = _dec(pos.get("mark") or pos.get("mark_price") or pos.get("price"))
            nominal = _dec(pos.get("notional") or pos.get("notional_usd"))
            pos_upnl = _dec(pos.get("unrealized_pnl") or pos.get("upnl"))
            side = (pos.get("side") or "").lower() or ("long" if (qty or 0) > 0 else "short")
            side_color = "green" if side in ("long", "buy") else "red"

            if nominal is None and qty is not None and mark is not None:
                nominal = abs(qty * mark)
            if nominal:
                total_notional += nominal
            if pos_upnl:
                # Already aggregated via the profile-level upnl, but track
                # for the per-position table header consistency check.
                pass

            upnl_cell = "—"
            if pos_upnl is not None:
                c = "green" if pos_upnl >= 0 else "red"
                upnl_cell = f"[{c}]{pos_upnl:+,.4f}[/]"

            tbl.add_row(
                pos.get("symbol") or "?",
                f"[{side_color}]{side}[/]",
                _fmt_qty(qty),
                _fmt_usd(entry, 4) if entry is not None else "—",
                _fmt_usd(mark, 4) if mark is not None else "—",
                _fmt_usd(nominal) if nominal is not None else "—",
                upnl_cell,
            )
            n_open += 1

        console.print(title)
        console.print(tbl)
        console.print()
        total_wallet += wallet
        total_upnl   += upnl

    # Footer totals
    upnl_color = "green" if total_upnl >= 0 else "red"
    console.print(
        f"[bold]Totals[/bold] across {len(data)} profile(s), {n_open} open position(s):  "
        f"wallet [bold]{_fmt_usd(total_wallet)}[/bold]  "
        f"· uPnL [bold {upnl_color}]{total_upnl:+,.2f}[/]  "
        f"· notional [dim]{_fmt_usd(total_notional)}[/dim]"
    )


# Convenience alias: ``talyxion positions`` mirrors ``talyxion list positions``.
@click.command(name="positions")
@click.option("--profile", "profile_filter", default=None,
              help="Filter to one profile by name or id.")
@click.option("--scope",
              type=click.Choice(["local", "all"]),
              default="all", show_default=True,
              help="Which profiles' positions to include.")
@click.pass_context
def positions_cmd(ctx, profile_filter: str | None, scope: str):
    """Alias of ``talyxion list positions`` — hiển thị danh mục."""
    ctx.invoke(list_positions, profile_filter=profile_filter, scope=scope)
