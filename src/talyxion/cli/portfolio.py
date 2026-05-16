"""Visibility commands: portfolio, balance, whoami, tier, show, doctor.

These are read-only commands that surface server-side state in the
terminal so users don't need to open the web dashboard for routine
checks. Every command exits cleanly on auth failure or revoked token
(same envelope as ``profiles.py``).
"""
from __future__ import annotations

import socket
from collections import defaultdict
from decimal import Decimal
from typing import Any

import click
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    FriendlyHTTPError,
    NotAuthenticatedError,
    TokenRevokedError,
    explain_http_failure,
)
from talyxion.cli.profiles import _dec, _exec_label, _fmt_qty, _fmt_usd, _status_color

console = Console()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _raw_get(path: str) -> dict[str, Any]:
    """Inner HTTP call — converts low-level exceptions into FriendlyHTTPError.

    Callers that want to render the error inline (visibility commands)
    should use :func:`_get`; callers that prefer to handle the headline
    themselves (``/doctor``) can call this directly and catch
    ``FriendlyHTTPError`` / ``NotAuthenticatedError`` / ``TokenRevokedError``.
    """
    try:
        with DeviceTokenClient() as client:
            return client.get(path)
    except (NotAuthenticatedError, TokenRevokedError):
        raise
    except Exception as exc:  # noqa: BLE001
        headline, hint = explain_http_failure(exc, path)
        raise FriendlyHTTPError(headline, hint) from exc


def _get(path: str) -> dict[str, Any]:
    """User-facing HTTP wrapper: print a panel + exit on error."""
    try:
        return _raw_get(path)
    except NotAuthenticatedError:
        console.print("[red]Not authenticated.[/red] Type /login to pair this device.")
        raise SystemExit(1)
    except TokenRevokedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    except FriendlyHTTPError as exc:
        console.print(f"[red]✗ {exc.headline}[/red]")
        if exc.hint:
            console.print(Panel(exc.hint, border_style="yellow", title="What to try"))
        raise SystemExit(1)


def _pct(num: Decimal, den: Decimal) -> str:
    if not den:
        return "—"
    try:
        return f"{(num / den) * 100:+.2f}%"
    except Exception:  # noqa: BLE001
        return "—"


# ---------------------------------------------------------------------------
# ``talyxion portfolio``
# ---------------------------------------------------------------------------


@click.command("portfolio")
@click.option(
    "--scope",
    type=click.Choice(["local", "all"]),
    default="all",
    show_default=True,
    help="Which profiles to roll up.",
)
@click.option(
    "--by",
    type=click.Choice(["exchange", "profile", "mode", "symbol"]),
    default="exchange",
    show_default=True,
    help="Group the wallet/P&L breakdown by this dimension.",
)
@click.option("--json", "as_json", is_flag=True, help="Print raw JSON instead of a table.")
def portfolio_cmd(scope: str, by: str, as_json: bool):
    """Aggregate portfolio: wallet, P&L, notional rolled up across profiles.

    Same data that powers the web dashboard's KPI cards, but with
    drill-down by exchange / profile / sim-vs-live / symbol. Heartbeat
    snapshots only — no exchange API calls.
    """
    payload = _get(f"/trading/profiles/?include={scope}")
    profiles = payload.get("data") or []
    if as_json:
        import json as _j
        console.print_json(_j.dumps(payload, indent=2))
        return
    if not profiles:
        console.print("[yellow]No profiles to summarise.[/yellow]")
        return

    # ── Totals ──────────────────────────────────────────────────────────
    total_wallet = Decimal("0")
    total_upnl   = Decimal("0")
    total_notional = Decimal("0")
    n_open = 0
    peak_equity = Decimal("0")
    latest_heartbeat = None

    # ── Bucket key picker ───────────────────────────────────────────────
    def _bucket_key(p: dict, pos: dict | None = None) -> str:
        if by == "exchange":
            return f'{p.get("exchange","?")}/{p.get("market_type","?")}'
        if by == "profile":
            return f'#{p.get("id","?")} {p.get("name","?")}'
        if by == "mode":
            return f'{p.get("mode","?")} · {p.get("execution_mode","?")}'
        if by == "symbol" and pos:
            return pos.get("symbol") or "?"
        return "?"

    # buckets[key] = {wallet, upnl, notional, count, profiles}
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "wallet": Decimal("0"), "upnl": Decimal("0"),
        "notional": Decimal("0"), "n_open": 0, "rows": set(),
    })

    for p in profiles:
        wallet = _dec(p.get("last_app_wallet_usd")) or Decimal("0")
        upnl   = _dec(p.get("last_app_unrealized_pnl")) or Decimal("0")
        peak   = _dec(p.get("peak_equity_usd")) or Decimal("0")
        positions = p.get("last_app_positions") or []
        hb = p.get("last_app_seen_at")

        total_wallet += wallet
        total_upnl   += upnl
        if peak > peak_equity:
            peak_equity = peak
        if hb and (latest_heartbeat is None or hb > latest_heartbeat):
            latest_heartbeat = hb

        if by == "symbol":
            for pos in positions:
                k = _bucket_key(p, pos)
                qty   = _dec(pos.get("qty") or pos.get("amount") or pos.get("size"))
                mark  = _dec(pos.get("mark") or pos.get("mark_price") or pos.get("price"))
                nominal = _dec(pos.get("notional") or pos.get("notional_usd"))
                if nominal is None and qty is not None and mark is not None:
                    nominal = abs(qty * mark)
                pos_upnl = _dec(pos.get("unrealized_pnl") or pos.get("upnl")) or Decimal("0")
                buckets[k]["notional"] += nominal or Decimal("0")
                buckets[k]["upnl"]     += pos_upnl
                buckets[k]["n_open"]   += 1
                buckets[k]["rows"].add(p.get("id"))
                total_notional += nominal or Decimal("0")
                n_open += 1
        else:
            k = _bucket_key(p)
            buckets[k]["wallet"] += wallet
            buckets[k]["upnl"]   += upnl
            buckets[k]["rows"].add(p.get("id"))
            for pos in positions:
                qty   = _dec(pos.get("qty") or pos.get("amount") or pos.get("size"))
                mark  = _dec(pos.get("mark") or pos.get("mark_price") or pos.get("price"))
                nominal = _dec(pos.get("notional") or pos.get("notional_usd"))
                if nominal is None and qty is not None and mark is not None:
                    nominal = abs(qty * mark)
                if nominal:
                    buckets[k]["notional"] += nominal
                    total_notional += nominal
                n_open += 1
                buckets[k]["n_open"] += 1

    # ── Header panel ────────────────────────────────────────────────────
    upnl_color = "green" if total_upnl >= 0 else "red"
    # Drawdown is "—" only when we genuinely cannot compute it — name the
    # reason next to the dash so the user knows whether to wait, run a
    # cycle, or open an issue.
    if peak_equity and total_wallet:
        dd = (total_wallet - peak_equity) / peak_equity * 100
        dd_color = "green" if dd >= 0 else "red"
        dd_text = f"[{dd_color}]{dd:+.2f}%[/]  vs peak {_fmt_usd(peak_equity)}"
    elif not latest_heartbeat:
        dd_text = "[yellow]—  no heartbeat yet — run /run --once to bootstrap[/yellow]"
    elif not peak_equity:
        dd_text = "[dim]—  peak equity not recorded yet (first cycle hasn't completed)[/dim]"
    else:
        dd_text = "[dim]—  wallet $0 — top up the exchange account[/dim]"

    header_lines = [
        f"[bold]Wallet[/bold]   {_fmt_usd(total_wallet)}",
        f"[bold]uPnL[/bold]     [{upnl_color}]{total_upnl:+,.2f}[/]  ({_pct(total_upnl, total_wallet)})",
        f"[bold]Notional[/bold] {_fmt_usd(total_notional)}",
        f"[bold]Open pos[/bold] {n_open}  · profiles: {len(profiles)}",
        f"[bold]Drawdown[/bold] {dd_text}",
    ]
    if latest_heartbeat:
        header_lines.append(f"[dim]Last heartbeat:[/dim] {latest_heartbeat}")
    console.print(Panel(
        "\n".join(header_lines),
        title=f"Portfolio · scope={scope}",
        border_style="cyan",
        box=box.ROUNDED,
    ))

    # ── Breakdown table ─────────────────────────────────────────────────
    title = f"By {by}"
    tbl = Table(title=title, show_lines=False, box=box.SIMPLE)
    tbl.add_column(by.capitalize(), style="bold")
    if by != "symbol":
        tbl.add_column("Wallet",   justify="right")
    tbl.add_column("uPnL",        justify="right")
    tbl.add_column("Notional",    justify="right")
    tbl.add_column("Open",        justify="right", style="dim")
    tbl.add_column("# Profiles",  justify="right", style="dim")
    tbl.add_column("Share",       justify="right", style="dim")

    sort_key = lambda kv: (-(kv[1]["notional"] if by == "symbol" else kv[1]["wallet"]),)
    for key, b in sorted(buckets.items(), key=sort_key):
        upnl = b["upnl"]
        upnl_c = "green" if upnl >= 0 else "red"
        denom = total_notional if by == "symbol" else total_wallet
        share_of = b["notional"] if by == "symbol" else b["wallet"]
        cells = [key]
        if by != "symbol":
            cells.append(_fmt_usd(b["wallet"]))
        cells.extend([
            f"[{upnl_c}]{upnl:+,.2f}[/]",
            _fmt_usd(b["notional"]),
            str(b["n_open"]),
            str(len(b["rows"])),
            _pct(share_of, denom) if denom else "—",
        ])
        tbl.add_row(*cells)
    console.print(tbl)
    console.print(
        "[dim]Snapshot from last heartbeat per profile — run "
        "[/dim][bold]talyxion run[/bold][dim] to refresh local profiles.[/dim]"
    )


# ---------------------------------------------------------------------------
# ``talyxion balance``
# ---------------------------------------------------------------------------


@click.command("balance")
def balance_cmd():
    """Wallet balance per credential (rolled up from each profile).

    Each exchange API key may back several profiles — this view sums the
    wallets reported by every profile that uses a given credential, so
    you see one row per actual API key.
    """
    profiles = _get("/trading/profiles/?include=all").get("data") or []
    if not profiles:
        console.print("[yellow]No profiles yet.[/yellow]")
        return

    # Group by (credential_id, exchange, label). ``has_heartbeat`` tracks
    # whether *any* profile under this credential has reported wallet
    # data — so we can replace ``$0.00`` with a clearer "no heartbeat"
    # hint when nothing has been seen yet.
    buckets: dict[tuple[int, str, str], dict[str, Any]] = defaultdict(lambda: {
        "wallet": Decimal("0"), "upnl": Decimal("0"),
        "n_profiles": 0, "validation": "—", "ip": None,
        "permissions": {}, "is_local_only": False,
        "has_heartbeat": False, "last_validation_error": None,
    })
    for p in profiles:
        cred = p.get("credential") or {}
        cid = cred.get("id") or 0
        key = (cid, cred.get("exchange") or "?", cred.get("label") or "?")
        b = buckets[key]
        wallet_raw = p.get("last_app_wallet_usd")
        upnl_raw = p.get("last_app_unrealized_pnl")
        if wallet_raw is not None or upnl_raw is not None or p.get("last_app_seen_at"):
            b["has_heartbeat"] = True
        b["wallet"] += _dec(wallet_raw) or Decimal("0")
        b["upnl"]   += _dec(upnl_raw) or Decimal("0")
        b["n_profiles"] += 1
        b["validation"]   = cred.get("validation_status", "?")
        b["ip"]           = cred.get("last_outbound_ip_seen")
        b["permissions"]  = cred.get("permissions") or {}
        b["is_local_only"] = bool(cred.get("is_local_only"))
        b["last_validation_error"] = cred.get("last_validation_error")

    tbl = Table(title="Balances by credential", box=box.SIMPLE)
    tbl.add_column("#",         style="dim", justify="right", width=4)
    tbl.add_column("Exchange",  style="cyan")
    tbl.add_column("Label",     style="bold")
    tbl.add_column("Wallet",    justify="right")
    tbl.add_column("uPnL",      justify="right")
    tbl.add_column("Profiles",  justify="right", style="dim")
    tbl.add_column("Storage")
    tbl.add_column("Status")
    tbl.add_column("Last IP",   style="dim")
    tbl.add_column("Perms",     style="dim")

    degraded_rows: list[str] = []  # collected for the footer hint
    for (cid, exch, label), b in sorted(buckets.items(), key=lambda kv: -kv[1]["wallet"]):
        upnl_c = "green" if b["upnl"] >= 0 else "red"
        storage = "[cyan]local-only[/cyan]" if b["is_local_only"] else "[magenta]server[/magenta]"
        v = b["validation"]
        v_color = {"ok": "green", "pending": "yellow"}.get(v, "red")

        # Wallet / uPnL: distinguish "$0 (real)" from "no heartbeat yet".
        if not b["has_heartbeat"]:
            wallet_cell = "[yellow]no heartbeat[/yellow]"
            upnl_cell = "[dim]—[/dim]"
            degraded_rows.append(f"#{cid} {exch}/{label}: no heartbeat yet — run /run --once")
        else:
            wallet_cell = _fmt_usd(b["wallet"])
            upnl_cell = f"[{upnl_c}]{b['upnl']:+,.2f}[/]"

        # Last IP: empty string vs missing.
        if b["ip"]:
            ip_cell = b["ip"]
        else:
            ip_cell = "[dim]not seen yet[/dim]"

        # Permissions: empty dict has multiple causes — disambiguate.
        perms = []
        for k, label_short in [
            ("canTrade", "trade"), ("canFutures", "fut"),
            ("canMargin", "marg"), ("canWithdraw", "withd⚠"),
        ]:
            if b["permissions"].get(k):
                perms.append(label_short)
        if perms:
            perms_cell = ",".join(perms)
        elif v == "pending":
            perms_cell = "[yellow]pending validation[/yellow]"
            degraded_rows.append(f"#{cid} {exch}/{label}: validation pending")
        elif v != "ok":
            err = b.get("last_validation_error") or "validation failed"
            perms_cell = f"[red]err[/red] [dim]{str(err)[:40]}[/dim]"
            degraded_rows.append(f"#{cid} {exch}/{label}: {err}")
        elif b["is_local_only"]:
            perms_cell = "[dim]local-only (not probed)[/dim]"
        else:
            perms_cell = "[dim]not reported by exchange[/dim]"

        tbl.add_row(
            str(cid),
            exch,
            label,
            wallet_cell,
            upnl_cell,
            str(b["n_profiles"]),
            storage,
            f"[{v_color}]{v}[/]",
            ip_cell,
            perms_cell,
        )
    console.print(tbl)
    if degraded_rows:
        console.print(
            "[dim]Notes:[/dim]\n  " + "\n  ".join(f"[dim]· {r}[/dim]" for r in degraded_rows)
        )


# ---------------------------------------------------------------------------
# ``talyxion whoami``
# ---------------------------------------------------------------------------


@click.command("whoami")
def whoami_cmd():
    """Account + device-token info reported by the server."""
    data = (_get("/trading/whoami/") or {}).get("data") or {}
    if not data:
        console.print("[red]Unexpected empty response from /trading/whoami/.[/red]")
        raise SystemExit(1)
    token = data.get("token") or {}
    billing = data.get("billing") or {}
    caps = data.get("tier_caps") or {}

    lines = [
        f"[bold]Email[/bold]     {data.get('email','—')}",
        f"[bold]User id[/bold]   {data.get('user_id','—')}",
        f"[bold]Tier[/bold]      [magenta]{(data.get('tier') or 'free').upper()}[/]"
        f"   billing: [dim]{billing.get('status','?')}[/]"
        + (f"   renews {billing['renews_at']}" if billing.get("renews_at") else ""),
        "",
        f"[bold]Device token[/bold]",
        f"  label      {token.get('label','—')}",
        f"  prefix     {token.get('prefix','—')}…",
        f"  scope      {token.get('scope','—')}",
        f"  created    {token.get('created_at','—')}",
        f"  last used  {token.get('last_used_at','—')}",
        "",
        f"[bold]Local profiles bound to this token[/bold]  "
        f"{data.get('local_profile_ids') or []}",
    ]
    if caps:
        lines.append("")
        lines.append("[bold]Tier capabilities[/bold]")
        for k, v in sorted(caps.items()):
            lines.append(f"  {k:<28} {v}")
    console.print(Panel(
        "\n".join(lines),
        title="whoami",
        border_style="magenta",
        box=box.ROUNDED,
    ))


# ---------------------------------------------------------------------------
# ``talyxion tier``
# ---------------------------------------------------------------------------


def _cap_label(value: Any, *, suffix: str = "", unlimited: str = "∞") -> str:
    """Format a tier-cap value. ``None`` means unlimited; missing → '?'."""
    if value is None:
        return unlimited
    if value == "" or value == "?":
        return "?"
    return f"{value}{suffix}"


@click.command("tier")
def tier_cmd():
    """Subscription tier + profile/credential quota usage.

    Quick "what can I still do" view — shows where you are vs your tier's
    caps so you know if creating one more live profile will get rejected
    or if you have headroom.

    The key names below must match :data:`accounts.subscriptions.TIER_CAPS`
    in the server. Earlier versions of this command read
    ``sim_profile_quota`` / ``live_profile_quota`` / ``credential_quota`` /
    ``max_book_usd`` — keys the server never sent — so the panel rendered
    "?" everywhere. Stick to the real keys (``max_active_profiles``,
    ``max_live_profiles``, ``max_credentials``, ``max_book_usd_per_profile``).
    """
    me = (_get("/trading/whoami/") or {}).get("data") or {}
    profiles = _get("/trading/profiles/?include=all").get("data") or []
    creds = _get("/trading/credentials/").get("data") or []

    tier = (me.get("tier") or "free").lower()
    caps = me.get("tier_caps") or {}

    sim_used = sum(1 for p in profiles if p.get("mode") == "simulation"
                   and p.get("status") != "archived")
    live_used = sum(1 for p in profiles if p.get("mode") == "live"
                    and p.get("status") != "archived")
    active_used = sum(1 for p in profiles if p.get("status") != "archived")

    book_cap = caps.get("max_book_usd_per_profile")
    book_str = "∞" if book_cap is None else (
        _fmt_usd(book_cap, 0) if book_cap not in ("", "?") else "?"
    )
    exchanges = caps.get("allowed_exchanges") or []
    rows = [
        ("Tier",                     tier.upper()),
        ("Active profiles",
            f"{active_used} / {_cap_label(caps.get('max_active_profiles'))}"),
        ("Live profiles used",
            f"{live_used} / {_cap_label(caps.get('max_live_profiles'))}"),
        ("Simulation profiles",      str(sim_used)),
        ("Credentials registered",
            f"{len(creds)} / {_cap_label(caps.get('max_credentials'))}"),
        ("Max leverage",             _cap_label(caps.get('max_leverage'), suffix="×")),
        ("Min cycle interval",       _cap_label(caps.get('min_cycle_interval_sec'), suffix="s")),
        ("Max book USD/profile",     book_str),
        ("Allowed exchanges",        ", ".join(exchanges) if exchanges else "?"),
        ("Live mode enabled",        "yes" if caps.get("live_mode_enabled") else "no"),
    ]
    tbl = Table(show_header=False, box=box.SIMPLE)
    tbl.add_column("", style="dim")
    tbl.add_column("", style="bold")
    for k, v in rows:
        tbl.add_row(k, str(v))
    console.print(Panel(tbl, title=f"Tier — {tier.upper()}",
                        border_style="cyan", box=box.ROUNDED))
    console.print(
        "[dim]Upgrade at [link]https://talyxion.com/pricing/[/link] to lift caps.[/dim]"
    )


# ---------------------------------------------------------------------------
# ``talyxion show <profile>``
# ---------------------------------------------------------------------------


@click.command("show")
@click.argument("profile", required=True)
def show_cmd(profile: str):
    """Detailed view of one profile: full config, credential, last heartbeat.

    Accepts either the numeric id or a name substring.
    """
    profiles = _get("/trading/profiles/?include=archived").get("data") or []
    if not profiles:
        console.print("[yellow]No profiles available.[/yellow]")
        return
    needle = profile.strip().lower()
    match = [
        p for p in profiles
        if needle == str(p.get("id", "")).lower() or needle in (p.get("name") or "").lower()
    ]
    if not match:
        console.print(f"[red]No profile matching '{profile}'.[/red]")
        raise SystemExit(1)
    if len(match) > 1:
        console.print(f"[yellow]Multiple matches — use the id:[/yellow]")
        for p in match:
            console.print(f"  #{p['id']:>4}  {p['name']}")
        raise SystemExit(1)

    p = match[0]
    cred = p.get("credential") or {}
    status = p.get("status", "?")

    cfg = Table(show_header=False, box=box.SIMPLE_HEAD)
    cfg.add_column("", style="dim")
    cfg.add_column("", style="bold")
    cfg.add_row("Id",                 str(p.get("id", "?")))
    cfg.add_row("Name",               p.get("name", "—"))
    cfg.add_row("Alpha",              p.get("alpha_id") or "—")
    cfg.add_row("Exchange",
        f'{p.get("exchange","?")} · {p.get("market_type","?")} · '
        f'{p.get("position_mode","?")} · {p.get("margin_mode","?")}')
    cfg.add_row("Execution",          p.get("execution_mode", "?"))
    cfg.add_row("Mode",               p.get("mode", "?"))
    cfg.add_row("Status",
        f"[{_status_color(status)}]{status}[/]"
        + (f"  ({p['pause_reason']})" if p.get("pause_reason") else ""))
    cfg.add_row("Leverage",           f'{p.get("order_leverage","?")}×')
    cfg.add_row("Cycle interval",     f'{p.get("cycle_interval_sec","?")}s')
    cfg.add_row("Profile book USD",   _fmt_usd(p.get("profile_book_usd"), 0))
    cfg.add_row("Volume USD div",     str(p.get("volume_usd_divisor", "?")))
    cfg.add_row("Max pos USD",        _fmt_usd(p.get("max_position_usd")))
    cfg.add_row("Max drawdown %",     str(p.get("max_drawdown_pct") or "—"))
    cfg.add_row("Symbol blocklist",
        ", ".join(p.get("symbol_blocklist") or []) or "—")

    cred_tbl = Table(show_header=False, box=box.SIMPLE_HEAD)
    cred_tbl.add_column("", style="dim")
    cred_tbl.add_column("", style="bold")
    cred_tbl.add_row("Id",        str(cred.get("id", "?")))
    cred_tbl.add_row("Label",     cred.get("label", "—"))
    cred_tbl.add_row("Exchange",  cred.get("exchange", "—"))
    cred_tbl.add_row("Storage",
        "local-only" if cred.get("is_local_only") else "server-side")
    cred_tbl.add_row("Validation",cred.get("validation_status", "—"))
    cred_tbl.add_row("Last IP",   cred.get("last_outbound_ip_seen") or "—")
    cred_tbl.add_row("Permissions",
        ", ".join(k for k, v in (cred.get("permissions") or {}).items() if v) or "—")
    cred_tbl.add_row("Fingerprint",
        (cred.get("api_key_fingerprint") or "—")[:24] + "…"
        if cred.get("api_key_fingerprint") else "—")

    hb_tbl = Table(show_header=False, box=box.SIMPLE_HEAD)
    hb_tbl.add_column("", style="dim")
    hb_tbl.add_column("", style="bold")
    hb_tbl.add_row("Last seen",   p.get("last_app_seen_at") or "—")
    # ``last_app_version`` is either a semver string ("0.4.2") from a
    # CLI heartbeat or the literal "server" tag written by the Celery
    # cycle (see _capture_server_heartbeat in crypto_user/tasks.py).
    # Surface that distinction so the user understands which side
    # produced the snapshot.
    raw_ver = (p.get("last_app_version") or "").strip()
    if not raw_ver:
        ver_label = "—"
    elif raw_ver == "server":
        ver_label = "[magenta]server-side cycle[/magenta]"
    else:
        ver_label = f"CLI {raw_ver}"
    hb_tbl.add_row("Heartbeat source", ver_label)
    hb_tbl.add_row("Outbound IP", p.get("last_app_outbound_ip") or "—")
    hb_tbl.add_row("Wallet",      _fmt_usd(p.get("last_app_wallet_usd")))
    upnl = _dec(p.get("last_app_unrealized_pnl"))
    upnl_str = "—"
    if upnl is not None:
        c = "green" if upnl >= 0 else "red"
        upnl_str = f"[{c}]{upnl:+,.2f}[/]"
    hb_tbl.add_row("uPnL",        upnl_str)
    hb_tbl.add_row("Peak equity", _fmt_usd(p.get("peak_equity_usd")))
    hb_tbl.add_row("Open pos",    str(len(p.get("last_app_positions") or [])))

    console.print(Panel(cfg, title="Profile", border_style="cyan", box=box.ROUNDED))
    console.print(Panel(cred_tbl, title="Credential",
                        border_style="magenta", box=box.ROUNDED))
    console.print(Panel(hb_tbl, title="Last heartbeat",
                        border_style="green" if p.get("last_app_seen_at") else "dim",
                        box=box.ROUNDED))

    positions = p.get("last_app_positions") or []
    if positions:
        ptbl = Table(title=f"Open positions ({len(positions)})",
                     box=box.SIMPLE, show_lines=False)
        ptbl.add_column("Symbol", style="bold")
        ptbl.add_column("Side")
        ptbl.add_column("Qty",      justify="right")
        ptbl.add_column("Entry",    justify="right", style="dim")
        ptbl.add_column("Mark",     justify="right", style="dim")
        ptbl.add_column("Notional", justify="right")
        ptbl.add_column("uPnL",     justify="right")
        for pos in positions:
            qty   = _dec(pos.get("qty") or pos.get("amount") or pos.get("size"))
            entry = _dec(pos.get("entry") or pos.get("entry_price"))
            mark  = _dec(pos.get("mark") or pos.get("mark_price") or pos.get("price"))
            nominal = _dec(pos.get("notional") or pos.get("notional_usd"))
            if nominal is None and qty is not None and mark is not None:
                nominal = abs(qty * mark)
            pos_upnl = _dec(pos.get("unrealized_pnl") or pos.get("upnl"))
            side = (pos.get("side") or "").lower() or ("long" if (qty or 0) > 0 else "short")
            side_c = "green" if side in ("long", "buy") else "red"
            upnl_cell = "—"
            if pos_upnl is not None:
                c = "green" if pos_upnl >= 0 else "red"
                upnl_cell = f"[{c}]{pos_upnl:+,.4f}[/]"
            ptbl.add_row(
                pos.get("symbol") or "?",
                f"[{side_c}]{side}[/]",
                _fmt_qty(qty),
                _fmt_usd(entry, 4) if entry is not None else "—",
                _fmt_usd(mark, 4) if mark is not None else "—",
                _fmt_usd(nominal) if nominal is not None else "—",
                upnl_cell,
            )
        console.print(ptbl)


# ---------------------------------------------------------------------------
# ``talyxion doctor``
# ---------------------------------------------------------------------------


@click.command("doctor")
def doctor_cmd():
    """Self-check: server reachable, token valid, keychain accessible, etc.

    Prints a one-line PASS/FAIL per check. Run before opening an issue
    so the report can include the first failing check.
    """
    from urllib.parse import urlparse

    from talyxion.cli.device_token_client import base_url as _base_url
    from talyxion.cli.keyring_store import load_device_token, load_token_meta

    checks: list[tuple[str, str, str]] = []

    # 1. Device token in keychain?
    try:
        raw = load_device_token()
        meta = load_token_meta() or {}
        if raw:
            checks.append(("Keychain — device token", "PASS",
                           f"label={meta.get('label','?')} prefix={meta.get('prefix','?')}…"))
        else:
            checks.append(("Keychain — device token", "FAIL",
                           "no token — run `talyxion auth login`"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("Keychain — device token", "FAIL", f"keyring error: {exc}"))

    # 2. Server DNS + port
    try:
        host = urlparse(_base_url()).hostname or "talyxion.com"
        ip = socket.gethostbyname(host)
        checks.append(("DNS — server hostname", "PASS", f"{host} → {ip}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("DNS — server hostname", "FAIL", str(exc)))

    def _probe(path: str) -> tuple[str, str, dict | None]:
        """Run one API probe; never prints inline. Returns (result, detail, payload)."""
        try:
            payload = _raw_get(path)
        except NotAuthenticatedError:
            return ("FAIL", "no device token in keyring", None)
        except TokenRevokedError as exc:
            return ("FAIL", str(exc)[:120], None)
        except FriendlyHTTPError as exc:
            return ("FAIL", exc.headline, None)
        except Exception as exc:  # noqa: BLE001
            return ("FAIL", str(exc)[:120], None)
        return ("PASS", "", payload)

    # 3. /trading/whoami round-trip
    result, fail_msg, payload = _probe("/trading/whoami/")
    if result == "PASS":
        data = (payload or {}).get("data") or {}
        checks.append(("API — /trading/whoami/", "PASS",
                       f"tier={data.get('tier','?')} email={data.get('email','?')}"))
    else:
        checks.append(("API — /trading/whoami/", "FAIL", fail_msg))

    # 4. Profiles reachable
    result, fail_msg, payload = _probe("/trading/profiles/?include=all")
    if result == "PASS":
        profiles = (payload or {}).get("data") or []
        local = [p for p in profiles if p.get("execution_mode") == "local"]
        server_p = [p for p in profiles if p.get("execution_mode") == "server"]
        checks.append(("API — /trading/profiles/", "PASS",
                       f"{len(profiles)} total ({len(local)} local, {len(server_p)} server)"))
    else:
        checks.append(("API — /trading/profiles/", "FAIL", fail_msg))

    # 5. Credentials list
    result, fail_msg, payload = _probe("/trading/credentials/")
    if result == "PASS":
        creds = (payload or {}).get("data") or []
        bad = [c for c in creds if c.get("permissions", {}).get("canWithdraw")]
        if bad:
            checks.append(("API — /trading/credentials/", "WARN",
                           f"{len(creds)} cred(s); {len(bad)} with canWithdraw=true ⚠"))
        else:
            checks.append(("API — /trading/credentials/", "PASS",
                           f"{len(creds)} cred(s); none allow withdraw ✓"))
    else:
        checks.append(("API — /trading/credentials/", "FAIL", fail_msg))

    # 6. CLI version
    try:
        from talyxion.cli._version import __cli_version__
        # The server doesn't expose a /version endpoint yet, so the best
        # skew signal we have is whether the API probes above succeeded.
        # The 4xx hint already names "CLI newer than server" as the most
        # likely cause; surfacing local version here lets users include
        # it in bug reports.
        checks.append(("CLI version", "PASS", __cli_version__))
    except Exception as exc:  # noqa: BLE001
        checks.append(("CLI version", "FAIL", str(exc)))

    # ── Render ──────────────────────────────────────────────────────────
    tbl = Table(title="talyxion doctor", box=box.SIMPLE, show_lines=False)
    tbl.add_column("Check", style="bold")
    tbl.add_column("Result")
    tbl.add_column("Detail", style="dim")
    color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
    n_fail = 0
    for name, result, detail in checks:
        c = color.get(result, "white")
        tbl.add_row(name, f"[{c}]{result}[/]", detail)
        if result == "FAIL":
            n_fail += 1
    console.print(tbl)
    if n_fail:
        console.print(f"[red]{n_fail} check(s) failed.[/red]")
        raise SystemExit(1)
    console.print("[green]All checks OK.[/green]")
