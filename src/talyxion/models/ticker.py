"""Ticker info models matching `main/api/v1/views.py::ticker_info`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TickerLatestSignal(BaseModel):
    model_config = ConfigDict(extra="allow")

    date: str
    side: str
    conviction: float
    entry_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    rationale: str | None = None


class TickerStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    signals_30d: int
    win_rate: float | None = None
    avg_conviction: float | None = None


class TickerInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    latest_signal: TickerLatestSignal
    stats: TickerStats
