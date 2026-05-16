"""Native exchange adapters — direct REST integration (no ccxt).

Each adapter implements :class:`ExchangeAdapter` from ``_base``. The CLI
picks an adapter at runtime via :func:`get_adapter` based on the
``exchange`` string on the profile / credential.

Why direct REST instead of ccxt:
  * ccxt is a 5 MB dep with ad-hoc behaviour per exchange.
  * Direct REST = audit-able by anyone reading the official exchange docs.
  * Lighter binary, deterministic error mapping, less hidden retries.
  * Each adapter ~300 lines; clear separation per exchange.
"""
from __future__ import annotations

from talyxion.cli.exchanges._base import (
    AuthFailure,
    BalanceSnapshot,
    ExchangeAdapter,
    IPBlocked,
    InsufficientFunds,
    OpenOrder,
    OrderRejected,
    OrderResult,
    PermissionsSummary,
    Position,
)
from talyxion.cli.exchanges.binance import BinanceAdapter

__all__ = [
    "ExchangeAdapter",
    "BalanceSnapshot",
    "PermissionsSummary",
    "Position",
    "OpenOrder",
    "OrderResult",
    "AuthFailure",
    "IPBlocked",
    "InsufficientFunds",
    "OrderRejected",
    "get_adapter",
]


def get_adapter(exchange: str, *, testnet: bool = False) -> type[ExchangeAdapter]:
    """Return the adapter class for ``exchange``.

    Raises ``KeyError`` if no adapter exists yet. Caller constructs the
    instance with their own credentials::

        AdapterCls = get_adapter("binance")
        adapter = AdapterCls(api_key=..., api_secret=..., testnet=True)
    """
    registry: dict[str, type[ExchangeAdapter]] = {
        "binance": BinanceAdapter,
    }
    name = (exchange or "").strip().lower()
    if name not in registry:
        raise KeyError(
            f"No adapter for exchange '{exchange}'. "
            f"Supported: {sorted(registry)}."
        )
    return registry[name]
