"""Root `Talyxion` client."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from ._config import Config
from ._http import HttpClient
from .models.status import ApiStatus
from .resources.datafields import DatafieldsResource
from .resources.rates import RatesResource
from .resources.screener import ScreenerResource
from .resources.signals import SignalsResource
from .resources.simulations import SimulationsResource
from .resources.ticker import TickerHandle
from .streaming import Stream


class Talyxion:
    """Sync client for the Talyxion REST API and realtime streams.

    Auth: pass ``api_key`` or set ``TALYXION_API_KEY``. Override the host with
    ``base_url`` or ``TALYXION_BASE_URL``.

    Example:
        client = Talyxion(api_key="tk_...")
        for sig in client.signals.list(date="2026-04-27"):
            print(sig.ticker, sig.conviction)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_base: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = Config.resolve(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
        )
        self._http = HttpClient(self._config, transport=transport)

        self.signals = SignalsResource(self._http)
        self.screener = ScreenerResource(self._http)
        self.datafields = DatafieldsResource(self._http)
        self.rates = RatesResource(self._http)
        self.simulations = SimulationsResource(self._http)
        self.stream = Stream(self._config)

    @property
    def config(self) -> Config:
        return self._config

    def ticker(self, symbol: str) -> TickerHandle:
        return TickerHandle(self._http, symbol)

    def status(self) -> ApiStatus:
        body = self._http.get("/api/v1/status/")
        return ApiStatus.model_validate(body.get("data") or {})

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Talyxion:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Talyxion base_url={self._config.base_url!r}>"

    # Allow `client.raw_get('/api/v1/...', params=...)` for forward compatibility.
    def raw_get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._http.get(path, params=params)
