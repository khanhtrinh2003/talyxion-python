"""``/api/v1/alphas/`` — alpha research endpoints."""
from __future__ import annotations

from typing import Any

from ..models.alphas import (
    Alpha,
    AlphaDetail,
    OverfitReport,
    PnLSeries,
    SimulationResult,
)
from ..models.common import Page
from ._base import Resource, build_page, extract_data


class AlphasResource(Resource):
    """Read + simulate alphas. Use ``client.alphas`` on a ``Talyxion`` client."""

    def list(
        self,
        *,
        mine_only: bool = False,
        region: str | None = None,
        universe: str | None = None,
        submitted: bool | None = None,
        min_sharpe: float | None = None,
        max_sharpe: float | None = None,
        min_returns: float | None = None,
        sort: str = "updated_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> Page[Alpha]:
        """``GET /api/v1/alphas/`` — paginated list with filters."""
        params: dict[str, Any] = {
            "mine_only": "1" if mine_only else None,
            "region": region,
            "universe": universe,
            "submitted": ("1" if submitted else "0") if submitted is not None else None,
            "min_sharpe": min_sharpe,
            "max_sharpe": max_sharpe,
            "min_returns": min_returns,
            "sort": sort,
            "order": order,
            "limit": limit,
            "offset": offset,
        }
        body = self._http.get("/api/v1/talyxion/alphas/", params=params)
        page: Page[Alpha] = build_page(body, Alpha, list(extract_data(body) or []))

        def _loader(lim: int, off: int) -> Page[Alpha]:
            return self.list(
                mine_only=mine_only, region=region, universe=universe,
                submitted=submitted, min_sharpe=min_sharpe, max_sharpe=max_sharpe,
                min_returns=min_returns, sort=sort, order=order,
                limit=lim, offset=off,
            )
        return page.with_loader(_loader)

    def get(self, alpha_id: str) -> AlphaDetail:
        """``GET /api/v1/alphas/{id}/`` — full detail (incl. code if licensed)."""
        body = self._http.get(f"/api/v1/talyxion/alphas/{alpha_id}/")
        return AlphaDetail.model_validate(extract_data(body))

    def pnl(self, alpha_id: str) -> PnLSeries:
        """``GET /api/v1/alphas/{id}/pnl/`` — equity curve series."""
        body = self._http.get(f"/api/v1/talyxion/alphas/{alpha_id}/pnl/")
        return PnLSeries.model_validate(extract_data(body))

    def overfit(self, alpha_id: str) -> OverfitReport:
        """``GET /api/v1/alphas/{id}/overfit/`` — payload + 5 checks."""
        body = self._http.get(f"/api/v1/talyxion/alphas/{alpha_id}/overfit/")
        return OverfitReport.model_validate(extract_data(body))

    def simulate_regular(
        self,
        code: str,
        *,
        region: str,
        universe: str,
        delay: int = 1,
        decay: int = 0,
        truncation: float = 0.0,
        neutralization: str = "NONE",
        long_only: bool = False,
        save: bool = True,
    ) -> SimulationResult:
        """Run a Regular alpha simulation. Returns the saved alpha id + metrics."""
        body = self._http.post("/api/v1/talyxion/alphas/simulate-regular/", json={
            "code": code,
            "region": region,
            "universe": universe,
            "delay": delay,
            "decay": decay,
            "truncation": truncation,
            "neutralization": neutralization,
            "long_only": int(bool(long_only)),
            "save": save,
        })
        return SimulationResult.model_validate(extract_data(body))

    def simulate_super(
        self,
        selections: list[str],
        combo: str,
        *,
        region: str,
        universe: str,
        decay: int = 0,
        truncation: float = 0.0,
        neutralization: str = "NONE",
        limit_selection: int = 10,
        save: bool = True,
    ) -> SimulationResult:
        """Run a Super (combo) alpha simulation."""
        body = self._http.post("/api/v1/talyxion/alphas/simulate-super/", json={
            "selections": selections,
            "combo": combo,
            "region": region,
            "universe": universe,
            "decay": decay,
            "truncation": truncation,
            "neutralization": neutralization,
            "limit_selection": limit_selection,
            "save": save,
        })
        return SimulationResult.model_validate(extract_data(body))
