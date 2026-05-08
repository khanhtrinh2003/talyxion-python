"""Talyxion Python SDK — official sync client for the Talyxion API.

```python
from talyxion import Talyxion

client = Talyxion(api_key="tk_...")
print(client.status())
```
"""

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
    ApiStatus,
    Datafield,
    DatafieldDetail,
    FeedEvent,
    Page,
    Pagination,
    RatesSeries,
    RatesSnapshot,
    RatesSuggestion,
    ResponseMeta,
    ScreenerItem,
    Signal,
    SignalHistoryItem,
    SimulationProgressEvent,
    SimulationStatus,
    TickerInfo,
    TickerLatestSignal,
    TickerStats,
    YahooQuote,
)

__all__ = [
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
    "Talyxion",
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
    "TickerInfo",
    "TickerLatestSignal",
    "TickerStats",
    "YahooQuote",
    "__version__",
]
