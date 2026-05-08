"""Signal models matching `main/api/v1/views.py::signals_list/history/screener`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Signal(BaseModel):
    model_config = ConfigDict(extra="allow")

    date: str
    ticker: str
    asset_class: str
    side: str
    conviction: float
    entry_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    expected_return_pct: float | None = None
    source: str | None = None
    rationale: str | None = None
    outcome: str | None = None


class SignalHistoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    date: str
    side: str
    conviction: float
    entry_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    outcome: str | None = None
    realized_return_pct: float | None = None


class ScreenerItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticker: str
    asset_class: str
    side: str
    conviction: float
    entry_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    rationale: str | None = None
