"""`/api/v1/rates-terminal/*`."""

from __future__ import annotations

from ..models.rates import RatesSeries, RatesSnapshot, RatesSuggestion, YahooQuote
from ._base import Resource, extract_data


class RatesResource(Resource):
    def snapshot(self, *, refresh: bool = False) -> RatesSnapshot:
        params = {"refresh": "true"} if refresh else None
        body = self._http.get("/api/v1/rates-terminal/data/", params=params)
        return RatesSnapshot(data=extract_data(body))

    def series(self, id: str, *, chart: bool | None = None) -> RatesSeries:
        params: dict[str, str] = {"id": id}
        if chart is not None:
            params["chart"] = "true" if chart else "false"
        body = self._http.get("/api/v1/rates-terminal/series/", params=params)
        return RatesSeries(id=id, data=extract_data(body))

    def suggest(self, q: str, *, limit: int = 10) -> list[RatesSuggestion]:
        body = self._http.get("/api/v1/rates-terminal/suggest/", params={"q": q, "limit": limit})
        items = extract_data(body) or []
        return [RatesSuggestion.model_validate(it) for it in items]

    def yahoo(self, symbol: str) -> YahooQuote:
        body = self._http.get("/api/v1/rates-terminal/yahoo/", params={"symbol": symbol})
        return YahooQuote(symbol=symbol, data=extract_data(body))
