"""Local risk gates — defense in depth.

These are NOT the source of truth — the server runs the same checks on
``cycle-report`` and pauses the profile on violations. Their purpose here:

  * Prevent obviously-bad orders before round-tripping to the exchange
    (saves latency + exchange rate-limit budget).
  * Give the user fast, visible "halted because drawdown 12% > cap 10%"
    feedback on the next cycle, not 5 minutes after the report posts.

Each gate returns ``(allowed: bool, reason: str)``. Reason is logged + sent
in the cycle-report ``log_excerpt`` so the web dashboard surfaces it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    # Optional adjusted USD amount (for clamping rather than hard-reject).
    clamped_usd: Decimal | None = None


def drawdown_gate(
    *,
    wallet_balance_usd: Decimal,
    peak_equity_usd: Decimal,
    max_drawdown_pct: Decimal | None,
) -> GateDecision:
    """Halt all order submission if current equity is drawn down beyond cap.

    Profile-level: returns a single decision for the whole cycle. The runner
    skips ``create_market_order`` for every target if this returns False.
    """
    if max_drawdown_pct is None or peak_equity_usd <= 0:
        return GateDecision(allowed=True)
    dd_pct = (peak_equity_usd - wallet_balance_usd) / peak_equity_usd * Decimal("100")
    if dd_pct > max_drawdown_pct:
        return GateDecision(
            allowed=False,
            reason=f"drawdown {dd_pct:.2f}% exceeds cap {max_drawdown_pct:.2f}% — orders skipped",
        )
    return GateDecision(allowed=True)


def position_size_gate(
    *,
    target_usd: Decimal,
    current_notional_usd: Decimal,
    max_position_usd: Decimal | None,
) -> GateDecision:
    """Clamp the resulting position to ``max_position_usd``.

    Returns a ``clamped_usd`` value the runner should use instead of the raw
    delta. If clamping would leave a delta below ~1 USD, the order is
    suppressed altogether.
    """
    if max_position_usd is None or max_position_usd <= 0:
        return GateDecision(allowed=True, clamped_usd=target_usd)

    # Clamp the resulting absolute notional.
    if target_usd >= 0:
        clamped = min(target_usd, max_position_usd)
    else:
        clamped = max(target_usd, -max_position_usd)

    delta = clamped - current_notional_usd
    if abs(delta) < Decimal("1"):
        return GateDecision(
            allowed=False,
            reason=f"delta {delta} below min lot tolerance",
            clamped_usd=clamped,
        )
    return GateDecision(allowed=True, clamped_usd=clamped)


def blocklist_gate(*, symbol: str, blocklist: Iterable[str]) -> GateDecision:
    if symbol in set(blocklist or ()):
        return GateDecision(allowed=False, reason=f"symbol {symbol} is on the profile blocklist")
    return GateDecision(allowed=True)


def withdraw_gate(*, permissions: dict | None) -> GateDecision:
    """Final pre-trade sanity: refuse if the credential somehow has withdraw on.

    Mirrors the server-side enforcement in credentials_create. Belt + braces
    in case a key's permissions changed after registration.
    """
    if permissions and permissions.get("canWithdraw"):
        return GateDecision(
            allowed=False,
            reason="credential has canWithdraw=true — refusing to trade",
        )
    return GateDecision(allowed=True)
