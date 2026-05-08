"""`/api/v1/screener/`."""

from __future__ import annotations

from ..models.common import Page
from ..models.signal import ScreenerItem
from ._base import Resource, build_page, extract_data


class ScreenerResource(Resource):
    def run(
        self,
        *,
        asset_class: str | None = None,
        side: str | None = None,
        min_conviction: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ScreenerItem]:
        params = {
            "asset_class": asset_class,
            "side": side,
            "min_conviction": min_conviction,
            "limit": limit,
            "offset": offset,
        }
        body = self._http.get("/api/v1/screener/", params=params)
        page: Page[ScreenerItem] = build_page(body, ScreenerItem, list(extract_data(body) or []))

        def _loader(lim: int, off: int) -> Page[ScreenerItem]:
            return self.run(
                asset_class=asset_class,
                side=side,
                min_conviction=min_conviction,
                limit=lim,
                offset=off,
            )

        return page.with_loader(_loader)
