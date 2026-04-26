"""Rates terminal models. Backend payloads are opaque; expose as dicts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RatesSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: Any = None


class RatesSeries(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    data: Any = None


class RatesSuggestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    name: str | None = None
    type: str | None = None


class YahooQuote(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    data: Any = None
