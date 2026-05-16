"""``/order`` group + ``/tape`` — manual order management from the REPL.

Manual orders **bypass the cycle dispatcher entirely**. The CLI loads
credentials from the OS keyring and calls the exchange adapter directly
— no Celery, no segment recompute, no server round-trip for the trade
itself. The server still logs every fill via a fire-and-forget POST so
the audit trail in ``ProfileOrderEvent`` stays complete.

Slash commands wired in :mod:`talyxion.cli.repl`:

* ``/order place <symbol> <buy|sell> <usd|qty> [--limit <price>] [--profile <id>]``
* ``/order cancel <order-id> --symbol <SYMBOL> [--profile <id>]``
* ``/order list [--profile <id>] [--symbol X]``
* ``/order cancel-all [--profile <id>]``
* ``/tape [--profile <id>] [--symbol X] [--follow] [--since <iso>]``
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote as _url_quote

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    FriendlyHTTPError,
    NotAuthenticatedError,
    TokenRevokedError,
    explain_http_failure,
)
from talyxion.cli.exchanges import (
    AuthFailure,
    InsufficientFunds,
    IPBlocked,
    OpenOrder,
    OrderRejected,
    OrderResult,
    get_adapter,
)
from talyxion.cli.keyring_store import load_credential

console = Console()


# ---------------------------------------------------------------------------
# Profile + credential resolution
# ---------------------------------------------------------------------------


def _fetch_profiles() -> list[dict[str, Any]]:
    """List every profile owned by the device token — error envelope identical
    to the visibility commands so the same panel renders on 404 / 5xx."""
    try:
        with DeviceTokenClient() as client:
            payload = client.get("/trading/profiles/?include=all")
    except NotAuthenticatedError:
        console.print("[red]Not authenticated.[/red] Type /login first.")
        raise SystemExit(1)
    except TokenRevokedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        headline, hint = explain_http_failure(exc, "/trading/profiles/")
        console.print(f"[red]✗ {headline}[/red]")
        if hint:
            console.print(Panel(hint, border_style="yellow", title="What to try"))
        raise SystemExit(1)
    return payload.get("data") or []


def _pick_profile(profile_id: int | None) -> dict[str, Any]:
    """Resolve --profile flag to a profile dict, or prompt if ambiguous.

    Manual orders need both an exchange and a credential label, both of
    which we read off the chosen profile. If the user has exactly one
    profile we pick it silently; otherwise we either honour the flag or
    ask interactively.
    """
    profiles = _fetch_profiles()
    if not profiles:
        console.print(
            "[red]No profiles yet.[/red] Create one at "
            "https://talyxion.com/trading/profiles/new/ first."
        )
        raise SystemExit(1)

    if profile_id is not None:
        for p in profiles:
            if int(p.get("id") or -1) == profile_id:
                return p
        console.print(f"[red]Profile #{profile_id} not found.[/red]")
        raise SystemExit(1)

    # Auto-pick when only one active candidate exists.
    active = [p for p in profiles if p.get("status") not in ("archived",)]
    if len(active) == 1:
        return active[0]

    # Multiple profiles — let user pick.
    console.print("[bold]Pick a profile:[/bold]")
    for p in active:
        console.print(
            f"  [cyan]{p.get('id')}[/cyan]  {p.get('name','?')}  "
            f"[dim]{p.get('exchange','?')} · {p.get('mode','?')} · {p.get('status','?')}[/dim]"
        )
    pick = click.prompt(
        "  Profile id", type=int, default=active[0].get("id"),
    )
    for p in active:
        if int(p.get("id") or -1) == pick:
            return p
    raise SystemExit(1)


def _credential_for_profile(profile: dict[str, Any]) -> tuple[str, str, dict[str, str], bool]:
    """Return ``(exchange, label, keyring_payload, testnet)`` for ``profile``.

    Looks up the OS keyring entry registered by ``/add``. Raises with a
    friendly message if the credential isn't on this machine.
    """
    cred = profile.get("credential") or {}
    exchange = (cred.get("exchange") or "").lower()
    label = cred.get("label") or ""
    if not exchange or not label:
        console.print(
            f"[red]Profile #{profile.get('id')} has no credential bound.[/red]"
        )
        raise SystemExit(1)
    payload = load_credential(exchange, label)
    if not payload:
        console.print(
            f"[red]No keyring entry for [bold]{exchange}:{label}[/bold] on this machine.[/red]\n"
            f"[dim]Either run [bold]/add {exchange} --label {label}[/bold] here, "
            f"or move to a machine where you ran /add before.[/dim]"
        )
        raise SystemExit(1)
    testnet = bool(payload.get("testnet"))
    return exchange, label, payload, testnet


def _build_adapter(exchange: str, label: str, payload: dict[str, str], testnet: bool):
    AdapterCls = get_adapter(exchange)
    return AdapterCls(
        api_key=payload.get("api_key", ""),
        api_secret=payload.get("api_secret", ""),
        passphrase=payload.get("passphrase", ""),
        testnet=testnet,
        market_type=payload.get("market_type") or "spot",
    )


# ---------------------------------------------------------------------------
# Audit log — fire-and-forget POST so manual orders show up in /tape
# ---------------------------------------------------------------------------


def _record_audit(profile_id: int, result: OrderResult) -> None:
    """POST a notify-only ProfileOrderEvent in a background thread.

    Endpoint: ``POST /trading/profiles/<pk>/orders/manual/`` — idempotent
    on ``client_order_id``. We dispatch the HTTP call on a daemon thread
    so the user's terminal returns immediately after the order lands at
    the exchange — a slow or 404-ing audit endpoint must never block
    the order flow. Failures are silenced; the order is already on the
    exchange, the audit row is best-effort.

    ``raw_response`` is truncated to ~4 KB serialised to keep the audit
    payload bounded; the exchange's full response stays in CLI memory.
    """
    import json as _json
    raw = result.raw_response or {}
    try:
        raw_serialised = _json.dumps(raw)[:4096]
        raw_clipped = _json.loads(raw_serialised) if raw_serialised.endswith("}") else {"_clipped": raw_serialised}
    except Exception:  # noqa: BLE001
        raw_clipped = {}
    body = {
        "symbol": result.symbol,
        "side": result.side,
        "usd_amount": float(result.usd_amount),
        "leverage": result.leverage,
        "client_order_id": result.client_order_id,
        "exchange_order_id": result.exchange_order_id,
        "status": result.status,
        "raw_response": raw_clipped,
    }

    def _post() -> None:
        try:
            with DeviceTokenClient() as client:
                client.post(
                    f"/trading/profiles/{profile_id}/orders/manual/",
                    json=body,
                )
        except Exception:  # noqa: BLE001
            # Audit is best-effort; never bubble the failure to the user.
            pass

    threading.Thread(target=_post, daemon=True, name="talyxion-audit").start()


# ---------------------------------------------------------------------------
# Slash group: /order
# ---------------------------------------------------------------------------


@click.group("order", invoke_without_command=True)
@click.pass_context
def order_group(ctx: click.Context) -> None:
    """Manual order placement, cancellation, and listing.

    Bypasses the cycle dispatcher entirely — orders go straight from
    your machine to the exchange via the credential bound to the
    selected profile. Use ``/order place --help`` for the full flag set.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@order_group.command("place")
@click.argument("symbol")
@click.argument("side", type=click.Choice(["buy", "sell"], case_sensitive=False))
@click.argument("amount")
@click.option("--profile", "profile_id", type=int, default=None,
              help="Profile id whose credential to use. Auto-picks when only one exists.")
@click.option("--limit", "limit_price", default=None,
              help="Limit price. Omit for a market order.")
@click.option("--qty", "as_qty", is_flag=True,
              help="Interpret AMOUNT as base-currency quantity instead of USD notional.")
@click.option("-y", "--yes", "no_confirm", is_flag=True,
              help="Skip the y/N confirmation prompt.")
def order_place(symbol: str, side: str, amount: str,
                profile_id: int | None, limit_price: str | None,
                as_qty: bool, no_confirm: bool) -> None:
    """Place a market or limit order on the exchange directly.

    Examples:

      \b
      /order place BTCUSDT buy 50
      /order place BTCUSDT buy 0.0008 --qty --limit 64000
      /order place ETHUSDT sell 100 --profile 9
    """
    try:
        amount_dec = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        console.print(f"[red]Invalid amount: {amount!r}[/red]")
        raise SystemExit(1)
    if amount_dec <= 0:
        console.print("[red]Amount must be positive.[/red]")
        raise SystemExit(1)

    limit_dec: Decimal | None = None
    if limit_price is not None:
        try:
            limit_dec = Decimal(str(limit_price))
        except (InvalidOperation, TypeError):
            console.print(f"[red]Invalid limit price: {limit_price!r}[/red]")
            raise SystemExit(1)

    profile = _pick_profile(profile_id)
    exchange, label, payload, testnet = _credential_for_profile(profile)

    # Build the preview panel so the user sees exactly what's about to hit
    # the exchange. Important for limit orders where a typo turns a $50
    # buy into a "buy 5,000,000 USDT @ $1" by mistake.
    kind = "LIMIT" if limit_dec is not None else "MARKET"
    amount_label = f"{amount_dec} {symbol.replace('USDT','')}" if as_qty else f"${amount_dec}"
    price_line = f"  Price:    [yellow]{limit_dec}[/yellow]\n" if limit_dec else ""
    env_warn = "[yellow]testnet[/yellow]" if testnet else "[red]MAINNET[/red]"
    console.print(Panel.fit(
        f"  Profile:  [cyan]#{profile.get('id')} {profile.get('name','?')}[/cyan]\n"
        f"  Account:  {exchange}:{label}  ({env_warn})\n"
        f"  Order:    [bold]{kind}[/bold]  [magenta]{side.upper()}[/magenta]  {symbol}\n"
        f"  Amount:   [bold]{amount_label}[/bold]\n"
        f"{price_line}"
        f"  Mode:     {profile.get('mode','?')}",
        title="/order place — review",
        border_style="cyan" if testnet else "red",
    ))

    # Sanity guard against fat-finger orders on mainnet. The threshold is
    # deliberately conservative ($10k notional) — anything larger should
    # be a deliberate decision typed twice. Testnet bypasses the prompt.
    if not testnet and not as_qty and amount_dec >= Decimal("10000"):
        console.print(
            f"[red]⚠ ${amount_dec} is a large notional on mainnet.[/red] "
            "[dim]Re-type the amount to confirm.[/dim]"
        )
        try:
            confirm_amount = click.prompt("  Re-enter amount (USD)", type=str)
        except (click.Abort, KeyboardInterrupt):
            console.print("[yellow]Aborted.[/yellow]")
            return
        try:
            if Decimal(str(confirm_amount)) != amount_dec:
                console.print("[red]✗ Amount mismatch — aborted.[/red]")
                raise SystemExit(1)
        except (InvalidOperation, TypeError):
            console.print("[red]✗ Couldn't parse confirmation — aborted.[/red]")
            raise SystemExit(1)

    if not no_confirm:
        if not click.confirm("  Submit?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Refuse the ambiguous combination ``--qty`` + market order. The
    # ``create_market_order`` adapter API takes USD notional; if the user
    # wants base-currency quantity for a market order they should compute
    # the equivalent USD themselves (we'd otherwise need a live mark
    # price snapshot here, which the CLI doesn't have on hand).
    if limit_dec is None and as_qty:
        console.print(
            "[red]✗ Market orders accept USD notional only (drop --qty).[/red]\n"
            "[dim]If you really want a base-currency market order, compute "
            "the USD equivalent yourself and retry without --qty. "
            "For limit orders --qty is supported because the limit price "
            "is already in the command.[/dim]"
        )
        raise SystemExit(1)

    client_order_id = f"manual-{uuid.uuid4().hex[:12]}"
    adapter = _build_adapter(exchange, label, payload, testnet)
    try:
        with adapter:
            if limit_dec is None:
                # Market order — adapter takes USD notional (translates
                # spot vs futures qty/notional internally).
                result = adapter.create_market_order(
                    symbol=symbol,
                    side=side,
                    usd_amount=amount_dec,
                    leverage=int(profile.get("order_leverage") or 1),
                    client_order_id=client_order_id,
                )
            else:
                # Limit order. Caller specifies qty (in base units) when
                # using --qty; we don't have a market snapshot to convert
                # USD → qty for limit orders, so refuse the implicit form.
                if not as_qty:
                    console.print(
                        "[red]✗ Limit orders need --qty (base-currency amount).[/red]\n"
                        "[dim]USD → qty conversion would require a live price snapshot we "
                        "don't have here. Compute the qty yourself and pass --qty.[/dim]"
                    )
                    raise SystemExit(1)
                result = adapter.create_limit_order(
                    symbol=symbol,
                    side=side,
                    qty=amount_dec,
                    price=limit_dec,
                    client_order_id=client_order_id,
                )
    except AuthFailure as exc:
        console.print(f"[red]✗ Auth rejected:[/red] {exc}")
        raise SystemExit(1)
    except IPBlocked as exc:
        console.print(f"[red]✗ IP blocked:[/red] {exc}")
        raise SystemExit(1)
    except InsufficientFunds as exc:
        console.print(f"[red]✗ Insufficient funds:[/red] {exc}")
        raise SystemExit(1)
    except NotImplementedError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ Order failed:[/red] {exc}")
        raise SystemExit(1)

    _print_order_result(result)
    _record_audit(int(profile.get("id") or 0), result)


def _print_order_result(result: OrderResult) -> None:
    color = {
        "filled": "green", "submitted": "cyan", "partial": "yellow",
        "rejected": "red",
    }.get(result.status, "white")
    body = (
        f"  Status:           [{color}]{result.status.upper()}[/]\n"
        f"  Symbol / side:    {result.symbol}  {result.side}\n"
        f"  Notional:         ${result.usd_amount}\n"
        f"  Exchange order:   {result.exchange_order_id or '—'}\n"
        f"  Client order id:  [dim]{result.client_order_id}[/dim]"
    )
    if result.error:
        body += f"\n  Error:            [red]{result.error}[/red]"
    console.print(Panel.fit(body, title="/order place — result", border_style=color))


@order_group.command("cancel")
@click.argument("order_id")
@click.option("--symbol", required=True, help="Symbol the order is on (BTCUSDT, ETHUSDT…).")
@click.option("--profile", "profile_id", type=int, default=None,
              help="Profile id whose credential to use. Auto-picks when only one exists.")
def order_cancel(order_id: str, symbol: str, profile_id: int | None) -> None:
    """Cancel one pending order by exchange order-id."""
    profile = _pick_profile(profile_id)
    exchange, label, payload, testnet = _credential_for_profile(profile)
    adapter = _build_adapter(exchange, label, payload, testnet)
    try:
        with adapter:
            ok = adapter.cancel_order(symbol=symbol, order_id=order_id)
    except NotImplementedError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ Cancel failed:[/red] {exc}")
        raise SystemExit(1)
    if ok:
        console.print(f"[green]✓ Cancelled order {order_id} on {symbol}.[/green]")
    else:
        console.print(
            f"[yellow]⚠ Server responded but status wasn't CANCELED — "
            f"check /order list for current state.[/yellow]"
        )


@order_group.command("list")
@click.option("--profile", "profile_id", type=int, default=None)
@click.option("--symbol", default=None,
              help="Filter by symbol (recommended on Binance — saves rate-limit).")
def order_list(profile_id: int | None, symbol: str | None) -> None:
    """List pending orders on the exchange for one profile."""
    profile = _pick_profile(profile_id)
    exchange, label, payload, testnet = _credential_for_profile(profile)
    adapter = _build_adapter(exchange, label, payload, testnet)
    try:
        with adapter:
            orders: list[OpenOrder] = adapter.fetch_open_orders(symbol=symbol)
    except NotImplementedError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ Fetch failed:[/red] {exc}")
        raise SystemExit(1)

    if not orders:
        console.print(
            f"[dim]No pending orders for [cyan]#{profile.get('id')} {profile.get('name')}[/cyan]"
            + (f" on {symbol}." if symbol else ".")
        )
        return

    tbl = Table(title=f"Pending orders · {profile.get('name')}", show_lines=False)
    tbl.add_column("Order id", style="dim")
    tbl.add_column("Symbol", style="bold")
    tbl.add_column("Side")
    tbl.add_column("Type")
    tbl.add_column("Price", justify="right")
    tbl.add_column("Qty", justify="right")
    tbl.add_column("Filled", justify="right", style="dim")
    tbl.add_column("Status")
    for o in orders:
        side_c = "green" if o.side == "buy" else "red"
        tbl.add_row(
            o.exchange_order_id,
            o.symbol,
            f"[{side_c}]{o.side}[/]",
            o.type,
            f"{o.price}",
            f"{o.qty}",
            f"{o.filled_qty}",
            o.status,
        )
    console.print(tbl)


@order_group.command("cancel-all")
@click.option("--profile", "profile_id", type=int, default=None)
@click.option("--symbol", default=None,
              help="Restrict cancel to one symbol (recommended).")
@click.option("-y", "--yes", "no_confirm", is_flag=True)
def order_cancel_all(profile_id: int | None, symbol: str | None,
                     no_confirm: bool) -> None:
    """Cancel every pending order for one profile (optionally one symbol)."""
    profile = _pick_profile(profile_id)
    exchange, label, payload, testnet = _credential_for_profile(profile)
    adapter = _build_adapter(exchange, label, payload, testnet)
    try:
        with adapter:
            orders: list[OpenOrder] = adapter.fetch_open_orders(symbol=symbol)
            if not orders:
                console.print("[dim]No pending orders to cancel.[/dim]")
                return
            preview = ", ".join(
                f"{o.symbol}#{o.exchange_order_id}" for o in orders[:5]
            )
            if len(orders) > 5:
                preview += f"  (+{len(orders)-5} more)"
            console.print(f"[yellow]About to cancel {len(orders)} order(s):[/yellow] {preview}")
            if not no_confirm and not click.confirm("  Proceed?", default=False):
                console.print("[yellow]Aborted.[/yellow]")
                return
            ok_n = 0
            fail_n = 0
            for o in orders:
                try:
                    if adapter.cancel_order(symbol=o.symbol, order_id=o.exchange_order_id):
                        ok_n += 1
                    else:
                        fail_n += 1
                except Exception:  # noqa: BLE001
                    fail_n += 1
    except NotImplementedError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ Cancel-all failed:[/red] {exc}")
        raise SystemExit(1)

    if fail_n:
        console.print(f"[yellow]Cancelled {ok_n}, failed {fail_n}.[/yellow]")
    else:
        console.print(f"[green]✓ Cancelled {ok_n} order(s).[/green]")


# ---------------------------------------------------------------------------
# Slash: /tape — live fills stream
# ---------------------------------------------------------------------------


@click.command("tape")
@click.option("--profile", "profile_id", type=int, default=None,
              help="Profile id to watch. Auto-picks if only one exists.")
@click.option("--symbol", default=None, help="Filter to one symbol.")
@click.option("--follow", "-f", is_flag=True, help="Stay live — print new fills as they arrive.")
@click.option("--limit", default=30, help="Initial backfill (default 30 rows).")
def tape_cmd(profile_id: int | None, symbol: str | None,
             follow: bool, limit: int) -> None:
    """Print recent order fills for one profile, optionally live.

    Polls ``/trading/profiles/<pk>/orders/?since=<ts>`` every 2 s in
    ``--follow`` mode. Phase 2.3 will swap this for a WebSocket
    subscription so fills land instantly.
    """
    profile = _pick_profile(profile_id)
    pid = int(profile.get("id") or 0)
    pname = profile.get("name", "?")

    def _print_rows(rows: list[dict[str, Any]]) -> str | None:
        latest_ts: str | None = None
        for row in rows:
            if symbol and row.get("symbol") != symbol:
                continue
            ts = (row.get("created_at") or "")[11:19]
            side = (row.get("side") or "").lower()
            side_c = "green" if side in ("buy", "long") else "red"
            st = (row.get("status") or "?").lower()
            st_c = {"filled": "green", "partial": "yellow",
                    "rejected": "red", "submitted": "cyan"}.get(st, "white")
            console.print(
                f"[dim]{ts}[/dim]  "
                f"[cyan]{pname}[/cyan]  "
                f"[{side_c}]{side:<4}[/]  "
                f"{row.get('symbol'):<10}  "
                f"${row.get('usd_amount', 0):>9.2f}  "
                f"[{st_c}]{st}[/]"
            )
            ts_full = row.get("created_at")
            if ts_full and (latest_ts is None or ts_full > latest_ts):
                latest_ts = ts_full
        return latest_ts

    # Initial backfill — newest first; reverse for chronological print.
    try:
        with DeviceTokenClient() as client:
            payload = client.get(
                f"/trading/profiles/{pid}/orders/?limit={limit}"
            )
    except Exception as exc:  # noqa: BLE001
        headline, hint = explain_http_failure(exc, f"/trading/profiles/{pid}/orders/")
        console.print(f"[red]✗ {headline}[/red]")
        if hint:
            console.print(Panel(hint, border_style="yellow", title="What to try"))
        raise SystemExit(1)
    rows = list(reversed(payload.get("data") or []))
    last_ts = _print_rows(rows)

    if not follow:
        return

    console.print("[dim]— following. Ctrl-C to stop. —[/dim]")
    try:
        while True:
            time.sleep(2.0)
            # URL-encode ``last_ts`` — ISO timestamps contain ``+`` for the
            # timezone offset, which the server otherwise sees as a space
            # after url-decoding and rejects with HTTP 400 bad_since.
            since_q = f"&since={_url_quote(last_ts)}" if last_ts else ""
            try:
                with DeviceTokenClient() as client:
                    payload = client.get(
                        f"/trading/profiles/{pid}/orders/?limit=20{since_q}"
                    )
            except Exception as exc:  # noqa: BLE001
                # Don't kill the loop on a transient blip; print + retry.
                console.print(f"[red]poll failed:[/red] {exc}")
                continue
            new_rows = list(reversed(payload.get("data") or []))
            if new_rows:
                ts = _print_rows(new_rows)
                if ts:
                    last_ts = ts
    except KeyboardInterrupt:
        console.print("\n[dim]— stopped. —[/dim]")
