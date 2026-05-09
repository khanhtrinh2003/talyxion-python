"""Talyxion Python SDK — official sync client for the Talyxion API.

Three core surfaces:

1. **Alpha research** — ``client.alphas`` + the ``Alpha`` / ``SuperAlpha``
   fluent builders. Run regular/super simulations, fetch PnL series, check
   overfit metrics.
2. **Trading desk** — ``client.trading``. CRUD trading credentials + profiles,
   activate/pause profiles, stream cycle history, query live positions.
3. **Marketplace** — ``client.market`` + ``client.wallet``. Search vetted
   alphas, buy with VND credits, list your own alpha for sale, manage wallet
   top-ups via VietQR.

```python
from talyxion import Talyxion, Backtest

tlx = Talyxion(api_key="tk_...")

# Alpha research
result = (
    Backtest(region="crypto_trade", universe="TOP19", decay=4)
    .alpha("rank(close - ts_mean(close, 20)) * volume")
    .simulate(tlx)
)
print(result.alpha_id, result.sharpe, result.passes_overfit())

# Deploy to trading desk
profile = tlx.trading.profiles.create(
    name="my_btc_v1", alpha_id=result.alpha_id,
    exchange="binance", credential_id=42,
    mode="simulation", leverage=2, book_usd=500,
).activate()

# Marketplace
for listing in tlx.market.search(min_sharpe=2.0, limit=10):
    print(listing.title, listing.price_vnd("lifetime"))
```
"""

from ._builders.alpha import Backtest
from ._version import __version__
from .client import Talyxion
from .errors import (
    TalyxionAuthError,
    TalyxionBadRequestError,
    TalyxionConnectionError,
    TalyxionError,
    TalyxionNotFoundError,
    TalyxionPermissionError,
    TalyxionRateLimitError,
    TalyxionResponseError,
    TalyxionServerError,
    TalyxionTierError,
)
from .models import (
    Alpha,
    AlphaDetail,
    ApiStatus,
    Credential,
    CreditTopUp,
    CycleRun,
    Datafield,
    DatafieldDetail,
    FeedEvent,
    License,
    Listing,
    ListingDetail,
    OverfitCheck,
    OverfitReport,
    Page,
    Pagination,
    PnLSeries,
    Position,
    PositionsSnapshot,
    Profile,
    Purchase,
    RatesSeries,
    RatesSnapshot,
    RatesSuggestion,
    ResponseMeta,
    ScreenerItem,
    Signal,
    SignalHistoryItem,
    SimulationProgressEvent,
    SimulationResult,
    SimulationStatus,
    TickerInfo,
    TickerLatestSignal,
    TickerStats,
    TopUpResponse,
    WalletAccount,
    WalletTransaction,
    YahooQuote,
)

__all__ = [
    # Client + builders
    "Talyxion",
    "Backtest",
    # Alpha models
    "Alpha",
    "AlphaDetail",
    "OverfitCheck",
    "OverfitReport",
    "PnLSeries",
    "SimulationResult",
    # Trading models
    "Credential",
    "CycleRun",
    "Position",
    "PositionsSnapshot",
    "Profile",
    # Market + wallet models
    "License",
    "Listing",
    "ListingDetail",
    "Purchase",
    "TopUpResponse",
    "CreditTopUp",
    "WalletAccount",
    "WalletTransaction",
    # Existing
    "ApiStatus",
    "Datafield",
    "DatafieldDetail",
    "FeedEvent",
    "Page",
    "Pagination",
    "RatesSeries",
    "RatesSnapshot",
    "RatesSuggestion",
    "ResponseMeta",
    "ScreenerItem",
    "Signal",
    "SignalHistoryItem",
    "SimulationProgressEvent",
    "SimulationStatus",
    "TickerInfo",
    "TickerLatestSignal",
    "TickerStats",
    "YahooQuote",
    # Errors
    "TalyxionAuthError",
    "TalyxionBadRequestError",
    "TalyxionConnectionError",
    "TalyxionError",
    "TalyxionNotFoundError",
    "TalyxionPermissionError",
    "TalyxionRateLimitError",
    "TalyxionResponseError",
    "TalyxionServerError",
    "TalyxionTierError",
    "__version__",
]
