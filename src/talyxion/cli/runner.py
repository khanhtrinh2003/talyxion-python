"""Cycle runner — the heart of ``talyxion run``.

For each local profile:

  1. GET ``/trading/profiles/<pk>/segment/`` → ``{symbol → target_usd}``.
  2. Load credentials from keyring, instantiate exchange adapter.
  3. ``fetch_balance()`` → wallet + current positions.
  4. Update peak_equity → compute drawdown → drawdown gate.
  5. For each target: blocklist gate, position-size gate, withdraw gate;
     submit ``create_market_order`` if all pass.
  6. POST ``/trading/profiles/<pk>/cycle-report/`` with outcome + orders.
  7. POST ``/trading/profiles/<pk>/heartbeat/`` with wallet snapshot.

Delete-aware sync: each loop fetches the full server-side profile list
and prunes local state for any profile that's archived. Token revocation
→ runner exits cleanly.
"""
from __future__ import annotations

import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from talyxion.cli._version import __cli_version__
from talyxion.cli.device_token_client import (
    DeviceTokenClient,
    NotAuthenticatedError,
    TokenRevokedError,
)
from talyxion.cli.exchanges import (
    AuthFailure,
    BalanceSnapshot,
    ExchangeAdapter,
    IPBlocked,
    InsufficientFunds,
    OrderRejected,
    OrderResult,
    get_adapter,
)
from talyxion.cli.keyring_store import load_credential
from talyxion.cli.logger import get_logger
from talyxion.cli.risk import (
    blocklist_gate,
    drawdown_gate,
    position_size_gate,
    withdraw_gate,
)
from talyxion.cli.state import (
    get_profile_state,
    load_state,
    prune_profile_state,
    save_state,
)

log = logging.getLogger("talyxion.cli")

# Map our internal cycle outcomes to the server's CycleOutcome enum.
OUTCOME_OK = "ok"
OUTCOME_AUTH_FAIL = "auth_fail"
OUTCOME_IP_BLOCKED = "ip_blocked"
OUTCOME_CONFLICT = "conflict"
OUTCOME_DATA_ERROR = "data_error"
OUTCOME_EXEC_ERROR = "exec_error"
OUTCOME_TIMEOUT = "timeout"


def _resolve_outbound_ip(state: dict[str, Any]) -> str:
    """Cache outbound IP for 1 hour to avoid hammering ipify.org."""
    cache = state.get("outbound_ip") or {}
    now = datetime.now(timezone.utc)
    cached_until = cache.get("cached_until")
    if cache.get("value") and cached_until:
        try:
            if datetime.fromisoformat(cached_until) > now:
                return cache["value"]
        except ValueError:
            pass
    try:
        ip = httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        ip = ""
    if ip:
        state["outbound_ip"] = {
            "value": ip,
            "cached_until": (now + timedelta(hours=1)).isoformat(),
        }
    return ip


def _decimal(v: Any, default: str = "0") -> Decimal:
    try:
        if v is None:
            return Decimal(default)
        return Decimal(str(v))
    except Exception:
        return Decimal(default)


def _due(pstate: dict[str, Any], cycle_interval_sec: int, now: datetime) -> bool:
    nxt = pstate.get("next_due_at")
    if not nxt:
        return True
    try:
        return datetime.fromisoformat(nxt) <= now
    except ValueError:
        return True


def _schedule_next(pstate: dict[str, Any], cycle_interval_sec: int, *, backoff: bool = False) -> None:
    base = cycle_interval_sec
    if backoff:
        # Exponential backoff capped at 30 min: 2x, 4x, 8x ...
        errors = int(pstate.get("consecutive_errors", 0) or 0)
        base = min(cycle_interval_sec * (2 ** errors), 1800)
    pstate["next_due_at"] = (datetime.now(timezone.utc) + timedelta(seconds=base)).isoformat()


def _build_order(
    *,
    adapter: ExchangeAdapter,
    cycle_id: str,
    symbol: str,
    target_usd: Decimal,
    current_notional: Decimal,
    leverage: int,
    perms: dict | None,
    blocklist: list[str],
    max_position_usd: Decimal | None,
) -> OrderResult | None:
    """Apply per-symbol risk gates + submit. Return None if gates suppress."""
    wd = withdraw_gate(permissions=perms)
    if not wd.allowed:
        log.warning("[%s] %s — %s", cycle_id, symbol, wd.reason)
        return None
    bl = blocklist_gate(symbol=symbol, blocklist=blocklist)
    if not bl.allowed:
        log.info("[%s] %s skipped (%s)", cycle_id, symbol, bl.reason)
        return None
    ps = position_size_gate(
        target_usd=target_usd,
        current_notional_usd=current_notional,
        max_position_usd=max_position_usd,
    )
    if not ps.allowed:
        log.info("[%s] %s skipped (%s)", cycle_id, symbol, ps.reason)
        return None
    final_target = ps.clamped_usd if ps.clamped_usd is not None else target_usd
    delta = final_target - current_notional
    if abs(delta) < Decimal("1"):
        return None

    side = "buy" if delta > 0 else "sell"
    coid = f"talyxion-{cycle_id}-{symbol}"
    log.info("[%s] %s %s $%.2f (lev=%d)", cycle_id, symbol, side, float(abs(delta)), leverage)
    return adapter.create_market_order(
        symbol=symbol,
        side=side,
        usd_amount=abs(delta),
        leverage=leverage,
        client_order_id=coid,
    )


def run_one_cycle(
    *,
    client: DeviceTokenClient,
    profile: dict[str, Any],
    state: dict[str, Any],
    dry_run: bool = False,
) -> str:
    """Execute one cycle for ``profile``. Returns outcome string."""
    pid = profile["id"]
    pstate = get_profile_state(state, pid)
    cycle_id = uuid.uuid4().hex[:16]
    started_at = datetime.now(timezone.utc)
    log.info("[%s] cycle start profile=%s name=%s", cycle_id, pid, profile.get("name"))

    cred_meta = profile.get("credential") or {}
    market_type = profile.get("market_type") or "spot"
    exchange = profile.get("exchange") or ""
    leverage = int(profile.get("order_leverage") or 1)
    cycle_interval_sec = int(profile.get("cycle_interval_sec") or 600)
    max_position_usd = _decimal(profile.get("max_position_usd")) if profile.get("max_position_usd") else None
    max_drawdown_pct = _decimal(profile.get("max_drawdown_pct")) if profile.get("max_drawdown_pct") else None
    blocklist = list(profile.get("symbol_blocklist") or [])
    perms = cred_meta.get("permissions") or {}

    # CRITICAL: profile.mode == "simulation" must skip every order
    # submission. The user explicitly opted out of real trades via the
    # web UI; the runner cannot ignore that signal. We treat it as an
    # implicit ``--dry-run`` for this cycle. ``balance`` and ``positions``
    # are still fetched so heartbeat + drawdown gate stay accurate.
    profile_mode = (profile.get("mode") or "live").lower()
    if profile_mode == "simulation" and not dry_run:
        log.info("[%s] profile.mode=simulation → skipping order submission "
                 "(balance/positions still fetched for heartbeat)", cycle_id)
        dry_run = True

    def _early_return(outcome_str: str, *, backoff: bool, last_err: str = "") -> str:
        """Persist state before bailing out so `talyxion status` reflects truth."""
        pstate["last_cycle_id"] = cycle_id
        pstate["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
        pstate["last_outcome"] = outcome_str
        if last_err:
            pstate["last_error"] = last_err[:300]
        if outcome_str != OUTCOME_OK:
            pstate["consecutive_errors"] = int(pstate.get("consecutive_errors", 0)) + 1
        _schedule_next(pstate, cycle_interval_sec, backoff=backoff)
        return outcome_str

    # ── 1. segment
    try:
        seg = client.get(f"/trading/profiles/{pid}/segment/")["data"]
    except httpx.HTTPStatusError as exc:
        sc = exc.response.status_code
        if sc == 409:
            log.error("[%s] credential conflict, profile auto-paused server-side", cycle_id)
            return _early_return(OUTCOME_CONFLICT, backoff=False,
                                 last_err="credential_conflict (server-side)")
        if sc == 422:
            log.error("[%s] segment data error: %s", cycle_id, exc.response.text[:200])
            return _early_return(OUTCOME_DATA_ERROR, backoff=True,
                                 last_err=exc.response.text[:200])
        raise

    # ── 2. credential + adapter
    creds = load_credential(exchange, cred_meta.get("label", ""))
    if not creds:
        msg = (f"no local keyring entry for {exchange}:{cred_meta.get('label')} — "
               f"run `talyxion add {exchange} --label {cred_meta.get('label','main')}`")
        log.error("[%s] %s", cycle_id, msg)
        return _early_return(OUTCOME_AUTH_FAIL, backoff=True, last_err=msg)

    AdapterCls = get_adapter(exchange)
    adapter: ExchangeAdapter = AdapterCls(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        passphrase=creds.get("passphrase", ""),
        testnet=bool(creds.get("testnet")),
        market_type=market_type,
    )

    outcome = OUTCOME_OK
    orders: list[OrderResult] = []
    log_lines: list[str] = []
    trades_attempted = 0
    trades_filled = 0
    drawdown_value = Decimal("0")
    wallet = BalanceSnapshot(Decimal("0"), Decimal("0"), [])

    try:
        # ── 3. fetch balance + positions
        wallet = adapter.fetch_balance()

        # ── 4. drawdown gate (profile-level)
        peak = max(Decimal(str(pstate.get("peak_equity_usd") or 0)), wallet.wallet_balance_usd)
        pstate["peak_equity_usd"] = float(peak)
        if peak > 0 and wallet.wallet_balance_usd < peak:
            drawdown_value = (peak - wallet.wallet_balance_usd) / peak

        dd = drawdown_gate(
            wallet_balance_usd=wallet.wallet_balance_usd,
            peak_equity_usd=peak,
            max_drawdown_pct=max_drawdown_pct,
        )
        if not dd.allowed:
            log.warning("[%s] %s — halting cycle", cycle_id, dd.reason)
            log_lines.append(f"DRAWDOWN HALT: {dd.reason}")
        else:
            # ── 5. iterate targets, submit orders
            cur_positions = {p.symbol: p for p in wallet.positions}
            for seg_block in seg.get("segments", []):
                for t in seg_block.get("targets", []):
                    sym = t.get("symbol", "")
                    if not sym:
                        continue
                    target_usd = _decimal(t.get("weight_usd"))
                    cur = cur_positions.get(sym)
                    current_notional = (
                        cur.notional_usd if cur and cur.side == "long"
                        else -cur.notional_usd if cur and cur.side == "short"
                        else Decimal("0")
                    )
                    trades_attempted += 1
                    if dry_run:
                        log.info("[%s] DRY-RUN %s target=%s current=%s",
                                 cycle_id, sym, target_usd, current_notional)
                        continue
                    try:
                        result = _build_order(
                            adapter=adapter,
                            cycle_id=cycle_id,
                            symbol=sym,
                            target_usd=target_usd,
                            current_notional=current_notional,
                            leverage=leverage,
                            perms=perms,
                            blocklist=blocklist,
                            max_position_usd=max_position_usd,
                        )
                    except InsufficientFunds as exc:
                        outcome = OUTCOME_EXEC_ERROR
                        log.warning("[%s] %s insufficient: %s", cycle_id, sym, exc)
                        orders.append(OrderResult(
                            client_order_id=f"talyxion-{cycle_id}-{sym}",
                            symbol=sym, side="?", usd_amount=Decimal("0"),
                            leverage=leverage, status="rejected", error=str(exc),
                        ))
                        continue
                    if result is None:
                        continue
                    orders.append(result)
                    if result.status in {"filled", "partial"}:
                        trades_filled += 1

    except AuthFailure as exc:
        outcome = OUTCOME_AUTH_FAIL
        log.error("[%s] auth failure: %s", cycle_id, exc)
        log_lines.append(f"AUTH FAILURE: {exc}")
    except IPBlocked as exc:
        outcome = OUTCOME_IP_BLOCKED
        log.error("[%s] IP blocked: %s — whitelist outbound IP on exchange", cycle_id, exc)
        log_lines.append(f"IP BLOCKED: {exc}")
    except Exception as exc:
        outcome = OUTCOME_EXEC_ERROR
        log.exception("[%s] unexpected cycle error: %s", cycle_id, exc)
        log_lines.append(f"UNEXPECTED: {exc}")
    finally:
        adapter.close()

    # ── 6. cycle-report
    finished_at = datetime.now(timezone.utc)
    try:
        client.post(f"/trading/profiles/{pid}/cycle-report/", json={
            "cycle_id": cycle_id,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "outcome": outcome,
            "trades_attempted": trades_attempted,
            "trades_filled": trades_filled,
            "effective_book_usd": float(wallet.wallet_balance_usd),
            "drawdown_value": float(drawdown_value),
            "log_excerpt": "\n".join(log_lines[-50:]),
            "orders": [o.to_json() for o in orders],
        })
    except httpx.HTTPStatusError as exc:
        sc = exc.response.status_code
        if sc == 409:
            log.info("[%s] duplicate cycle skipped server-side", cycle_id)
        elif sc == 422:
            log.error("[%s] cycle-report rejected (cap violation?): %s",
                      cycle_id, exc.response.text[:200])
            outcome = OUTCOME_EXEC_ERROR
        else:
            log.error("[%s] cycle-report HTTP %s: %s", cycle_id, sc, exc.response.text[:200])
    except Exception as exc:
        log.error("[%s] cycle-report failed: %s", cycle_id, exc)

    # ── 7. heartbeat
    try:
        client.post(f"/trading/profiles/{pid}/heartbeat/", json={
            "app_version": __cli_version__,
            "platform": f"{socket.gethostname()}",
            "outbound_ip": _resolve_outbound_ip(state),
            "wallet_balance_usd": float(wallet.wallet_balance_usd),
            "unrealized_pnl": float(wallet.unrealized_pnl),
            "positions": [p.to_json() for p in wallet.positions],
            "api_key_fingerprint": cred_meta.get("api_key_fingerprint", ""),
        })
    except Exception as exc:
        log.warning("[%s] heartbeat failed: %s", cycle_id, exc)

    # ── update state
    pstate["last_cycle_id"] = cycle_id
    pstate["last_cycle_at"] = finished_at.isoformat()
    pstate["last_outcome"] = outcome
    if outcome == OUTCOME_OK:
        pstate["consecutive_errors"] = 0
        _schedule_next(pstate, cycle_interval_sec, backoff=False)
    else:
        pstate["consecutive_errors"] = int(pstate.get("consecutive_errors", 0)) + 1
        _schedule_next(pstate, cycle_interval_sec, backoff=True)

    log.info("[%s] cycle done outcome=%s attempted=%d filled=%d",
             cycle_id, outcome, trades_attempted, trades_filled)
    return outcome


def run_loop(*, once: bool = False, only_profile: int | None = None, dry_run: bool = False) -> None:
    """Run the loop until SIGINT/SIGTERM (or once if ``once``)."""
    import signal as _signal

    log_real = get_logger()
    log_real.info("Runner starting (once=%s, profile=%s, dry_run=%s)", once, only_profile, dry_run)

    state = load_state()
    stop_flag = {"requested": False}

    def _sigterm(_signum, _frame):  # type: ignore[no-untyped-def]
        log_real.info("Stop signal received — finishing current cycle and exiting.")
        stop_flag["requested"] = True
    _signal.signal(_signal.SIGINT, _sigterm)
    _signal.signal(_signal.SIGTERM, _sigterm)

    try:
        with DeviceTokenClient() as client:
            while True:
                try:
                    server_profiles = client.get("/trading/profiles/")["data"]
                except TokenRevokedError as exc:
                    log_real.error("Token revoked: %s — exiting.", exc)
                    return
                except Exception as exc:
                    log_real.warning("Failed to fetch profiles: %s — retry in 60s", exc)
                    if once:
                        return
                    time.sleep(60)
                    continue

                live_ids = {p["id"] for p in server_profiles}
                dropped = prune_profile_state(state, live_ids)
                for d in dropped:
                    log_real.info("Profile %s archived on server — local state pruned.", d)

                now = datetime.now(timezone.utc)
                for p in server_profiles:
                    if only_profile is not None and p["id"] != only_profile:
                        continue
                    pstate = get_profile_state(state, p["id"])
                    interval = int(p.get("cycle_interval_sec") or 600)
                    # `--once` and `--profile <id>` are explicit user actions —
                    # always run at least one cycle, ignoring the schedule.
                    # Steady-state daemon mode (no flags) keeps the schedule
                    # honored so we don't hammer the exchange.
                    force = once or (only_profile is not None)
                    if not force and not _due(pstate, interval, now):
                        continue
                    try:
                        run_one_cycle(client=client, profile=p, state=state, dry_run=dry_run)
                    except TokenRevokedError as exc:
                        log_real.error("Token revoked mid-cycle: %s — exiting.", exc)
                        return
                    except Exception:
                        log_real.exception("Cycle crashed for profile %s — continuing.", p["id"])

                save_state(state)
                if once or stop_flag["requested"]:
                    log_real.info("Run complete.")
                    return

                # Sleep til next earliest due time (or 60s, whichever sooner).
                next_due = None
                for p in server_profiles:
                    if only_profile is not None and p["id"] != only_profile:
                        continue
                    ps = get_profile_state(state, p["id"])
                    nd = ps.get("next_due_at")
                    if nd:
                        try:
                            dt = datetime.fromisoformat(nd)
                            if next_due is None or dt < next_due:
                                next_due = dt
                        except ValueError:
                            pass
                sleep_for = 60
                if next_due:
                    sleep_for = max(1, int((next_due - datetime.now(timezone.utc)).total_seconds()))
                    sleep_for = min(sleep_for, 60)
                log_real.debug("Sleeping %ss until next due cycle.", sleep_for)
                time.sleep(sleep_for)
    except NotAuthenticatedError as exc:
        log_real.error("Not authenticated: %s", exc)
        return
    finally:
        save_state(state)
