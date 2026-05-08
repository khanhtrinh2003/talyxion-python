"""`/api/v1/signals/` and `/api/v1/signals/history/`."""

from __future__ import annotations

from datetime import date as date_t

from ..models.common import Page
from ..models.signal import Signal, SignalHistoryItem
from ._base import Resource, build_page, extract_data


class SignalsResource(Resource):
    def list(
        self,
        *,
        date: str | date_t | None = None,
        asset_class: str | None = None,
        side: str | None = None,
        min_conviction: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Signal]:
        """GET /api/v1/signals/"""
        params = {
            "date": date.isoformat() if isinstance(date, date_t) else date,
            "asset_class": asset_class,
            "side": side,
            "min_conviction": min_conviction,
            "limit": limit,
            "offset": offset,
        }
        body = self._http.get("/api/v1/signals/", params=params)
        page: Page[Signal] = build_page(body, Signal, list(extract_data(body) or []))

        def _loader(lim: int, off: int) -> Page[Signal]:
            return self.list(
                date=date,
                asset_class=asset_class,
                side=side,
                min_conviction=min_conviction,
                limit=lim,
                offset=off,
            )

        return page.with_loader(_loader)

    def history(
        self,
        ticker: str,
        *,
        days: int = 30,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[SignalHistoryItem]:
        """GET /api/v1/signals/history/"""
        params = {"ticker": ticker, "days": days, "limit": limit, "offset": offset}
        body = self._http.get("/api/v1/signals/history/", params=params)
        page: Page[SignalHistoryItem] = build_page(body, SignalHistoryItem, list(extract_data(body) or []))

        def _loader(lim: int, off: int) -> Page[SignalHistoryItem]:
            return self.history(ticker, days=days, limit=lim, offset=off)

        return page.with_loader(_loader)
