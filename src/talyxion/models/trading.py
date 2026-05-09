"""Pydantic models for trading desk endpoints."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Credential(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    exchange: str
    label: str
    account_uid: str | None = None
    validation_status: str = "unknown"
    last_validated_at: str | None = None
    last_outbound_ip_seen: str | None = None
    last_error: str = ""
    created_at: str


class Profile(BaseModel):
    """User trading profile — alpha + risk config + execution state."""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    exchange: str
    credential_id: int
    alpha_id: str
    region: str
    universe: str = ""
    data_exchange: str = ""
    market_type: str = "futures"
    position_mode: str = "one_way"
    margin_mode: str = "cross"
    order_leverage: int = 1
    profile_book_usd: float | None = None
    max_position_usd: float | None = None
    max_drawdown_pct: float | None = None
    volume_usd_divisor: int = 10
    cycle_interval_sec: int = 600
    mode: str = "simulation"
    status: str = "draft"
    pause_reason: str | None = None
    consecutive_errors: int = 0
    peak_equity_usd: float | None = None
    last_cycle_started_at: str | None = None
    last_cycle_finished_at: str | None = None
    last_cycle_id: str | None = None
    created_at: str
    updated_at: str


class CycleRun(BaseModel):
    """One execution of the cycle dispatcher against a profile."""

    model_config = ConfigDict(extra="allow")

    id: int
    profile_id: int
    outcome: str  # ok / skipped_locked / auth_fail / ip_blocked / conflict / data_error / exec_error / timeout
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    error_message: str = ""
    trades_attempted: int = 0
    trades_filled: int = 0
    drawdown_value: float | None = None
    effective_book_usd: float | None = None


class Position(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    side: str  # "long" | "short"
    qty: float
    entry_price: float | None = None
    mark_price: float | None = None
    notional: float | None = None
    unrealized_pnl: float | None = None
    pnl_pct: float | None = None
    leverage: int | None = None


class PendingOrder(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    side: str
    qty: float
    price: float | None = None
    order_type: str
    order_id: str | None = None
    client_order_id: str | None = None


class PositionsSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    wallet_balance: float | None = None
    unrealized_pnl: float | None = None
    available_balance: float | None = None
    total_notional: float | None = None
    long_notional: float | None = None
    short_notional: float | None = None
    long_count: int = 0
    short_count: int = 0
    position_count: int = 0
    positions: list[Position] = []
    pending_orders: list[PendingOrder] = []
