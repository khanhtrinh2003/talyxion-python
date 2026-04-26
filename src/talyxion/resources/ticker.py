"""`/api/v1/ticker/<ticker>/` — exposed as a handle."""

from __future__ import annotations

from .._http import HttpClient
from ..models.ticker import TickerInfo
from ._base import extract_data


class TickerHandle:
    """Lightweight wrapper bound to a single ticker symbol."""

    def __init__(self, http: HttpClient, ticker: str) -> None:
        self._http = http
        self.symbol = ticker.upper()

    def info(self) -> TickerInfo:
        body = self._http.get(f"/api/v1/ticker/{self.symbol}/")
        return TickerInfo.model_validate(extract_data(body))

    def __repr__(self) -> str:
        return f"<TickerHandle symbol={self.symbol}>"
