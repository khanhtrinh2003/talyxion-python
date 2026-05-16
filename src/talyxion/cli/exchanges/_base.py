"""Abstract base class + dataclasses for exchange adapters.

The CLI runner ([runner.py]) speaks to this interface only; per-exchange
specifics live in adapter modules. Each adapter must:

  1. Validate the credential on construction (or via ``validate()``)
     and return what permissions the key has (canTrade, canWithdraw, ...).
  2. Expose ``fetch_balance()``, ``fetch_positions()``, and
     ``create_market_order()`` with deterministic, exchange-agnostic shapes.
  3. Map exchange errors to one of: :class:`AuthFailure`,
     :class:`IPBlocked`, :class:`InsufficientFunds`, :class:`OrderRejected`.

The dataclass shapes are deliberately small — only what the cycle loop
and heartbeat payload need.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


# ── Exception hierarchy (mapped to outcome enum in trading_views) ────────


class ExchangeError(RuntimeError):
    """Base for all adapter errors."""


class AuthFailure(ExchangeError):
    """Credential rejected (invalid key, signature mismatch, revoked, …)."""


class IPBlocked(ExchangeError):
    """Exchange refused due to IP restriction / whitelist mismatch."""


class InsufficientFunds(ExchangeError):
    """Order rejected because account balance / margin is too low."""


class OrderRejected(ExchangeError):
    """Order failed for a non-fatal reason (min lot size, price filter, …)."""


# ── Result dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class PermissionsSummary:
    """Returned by ``validate_credentials()``.

    Used by ``talyxion add <exchange>`` to enforce ``canWithdraw=False``
    before registering the credential server-side.
    """

    can_trade: bool
    can_futures: bool = False
    can_margin: bool = False
    can_withdraw: bool = False
    account_uid: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "canTrade": self.can_trade,
            "canFutures": self.can_futures,
            "canMargin": self.can_margin,
            "canWithdraw": self.can_withdraw,
            **self.extra,
        }


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    notional_usd: Decimal
    upnl: Decimal
    side: str  # "long" | "short" | "flat"

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "qty": float(self.qty),
            "entry_price": float(self.entry_price),
            "notional_usd": float(self.notional_usd),
            "upnl": float(self.upnl),
            "side": self.side,
        }


@dataclass(frozen=True)
class BalanceSnapshot:
    """Wallet snapshot used for heartbeat + drawdown tracking."""

    wallet_balance_usd: Decimal
    unrealized_pnl: Decimal
    positions: list[Position]

    def to_json(self) -> dict[str, Any]:
        return {
            "wallet_balance_usd": float(self.wallet_balance_usd),
            "unrealized_pnl": float(self.unrealized_pnl),
            "positions": [p.to_json() for p in self.positions],
        }


@dataclass(frozen=True)
class OpenOrder:
    """One pending (unfilled) order on the exchange.

    Returned by ``fetch_open_orders``. Used by ``/order list`` and the
    dashboard's pending-orders pane. Shape is deliberately small — just
    what a trader needs to identify and cancel an order.
    """

    symbol: str
    side: str           # "buy" | "sell"
    type: str           # "market" | "limit" | "stop" | ...
    price: Decimal      # 0 for market orders
    qty: Decimal
    filled_qty: Decimal
    exchange_order_id: str
    client_order_id: str
    status: str         # "new" | "partially_filled" | "filled" | "canceled"
    created_at_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "type": self.type,
            "price": float(self.price),
            "qty": float(self.qty),
            "filled_qty": float(self.filled_qty),
            "exchange_order_id": self.exchange_order_id,
            "client_order_id": self.client_order_id,
            "status": self.status,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True)
class OrderResult:
    """Outcome of a single ``create_market_order()`` call."""

    client_order_id: str
    symbol: str
    side: str
    usd_amount: Decimal
    leverage: int
    status: str  # "submitted" | "filled" | "partial" | "rejected"
    exchange_order_id: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "usd_amount": float(self.usd_amount),
            "leverage": self.leverage,
            "status": self.status,
            "exchange_order_id": self.exchange_order_id,
            "raw_response": self.raw_response,
            "error": self.error,
        }


# ── Adapter ABC ───────────────────────────────────────────────────────


class ExchangeAdapter(abc.ABC):
    """Per-exchange REST integration.

    Adapter instances are short-lived (one per cycle). Do not cache secrets
    in module-level state; the runner constructs a fresh adapter each
    cycle from the keyring.
    """

    name: str = ""  # set by subclass — must match the registry key in __init__.py

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str = "",
        testnet: bool = False,
        market_type: str = "spot",  # "spot" | "futures"
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        self.market_type = market_type

    # ── lifecycle ────────────────────────────────────────────────
    @abc.abstractmethod
    def validate_credentials(self) -> PermissionsSummary:
        """Round-trip a permissions-introspection call. Raises ``AuthFailure``
        if the key is bad. Returns the permission summary on success."""

    @abc.abstractmethod
    def fetch_balance(self) -> BalanceSnapshot:
        """Read wallet USD balance + open positions."""

    @abc.abstractmethod
    def create_market_order(
        self,
        *,
        symbol: str,
        side: str,  # "buy" | "sell"
        usd_amount: Decimal,
        leverage: int,
        client_order_id: str,
    ) -> OrderResult:
        """Submit one market order. Raises adapter-specific exceptions on
        terminal failures (auth/IP). Returns ``OrderResult`` with
        status="rejected" for non-fatal rejections (lot size, etc.)."""

    # Manual order management — Phase 2.2. Marked non-abstract with a
    # NotImplementedError default so existing adapters (and tests that
    # use a mocked adapter) keep working until they explicitly opt in.

    def create_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        client_order_id: str,
        time_in_force: str = "GTC",
    ) -> OrderResult:
        """Submit one limit order. Subclasses override; default raises."""
        raise NotImplementedError(
            f"{self.name} adapter doesn't implement create_limit_order yet."
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> bool:
        """Cancel a pending order by exchange or client id. Returns True
        if the exchange acknowledged the cancel. Subclasses override."""
        raise NotImplementedError(
            f"{self.name} adapter doesn't implement cancel_order yet."
        )

    def fetch_open_orders(self, symbol: str | None = None) -> list[OpenOrder]:
        """List pending orders. Optional ``symbol`` filter. Subclasses override."""
        raise NotImplementedError(
            f"{self.name} adapter doesn't implement fetch_open_orders yet."
        )

    @abc.abstractmethod
    def close(self) -> None:
        """Release underlying HTTP client. Idempotent."""

    # Convenience: implement context-manager protocol so callers can::
    #     with adapter:
    #         adapter.fetch_balance()
    def __enter__(self) -> ExchangeAdapter:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
