"""Pydantic models for alpha research endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import pandas as pd


class Alpha(BaseModel):
    """Compact alpha row (list endpoints). No code/PnL — call ``.detail()`` for those."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    type: str = "REGULAR"
    author: str | None = None
    region: str = ""
    universe: str = ""
    neutralization: str = ""
    delay: int | None = None
    decay: int | None = None
    truncation: float | None = None
    long_only: bool | None = None
    sharpe: float | None = None
    fitness: float | None = None
    turnover: float | None = None
    drawdown: float | None = None
    returns_pct: float | None = None
    margin: float | None = None
    submitted: bool = False
    tags: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AlphaDetail(Alpha):
    """Full alpha — adds source code (regular) or selections+combo (super)."""

    code: str | None = None
    selections: str | None = None
    combo: str | None = None
    category: str | None = None
    descriptions: str | None = None
    favorite: bool = False


class OverfitCheck(BaseModel):
    key: str
    label: str
    result: str  # "Pass" | "Fail"
    passed: bool


class OverfitReport(BaseModel):
    """``GET /alphas/{id}/overfit/`` payload."""

    checks: list[OverfitCheck]
    payload: dict[str, Any] = Field(default_factory=dict)
    passes_all: bool


class PnLSeries(BaseModel):
    """Equity-curve series. Use ``.to_pandas()`` for analysis."""

    labels: list[str] = Field(default_factory=list)
    values: list[float | None] = Field(default_factory=list)

    def __len__(self) -> int:
        return min(len(self.labels), len(self.values))

    def to_pandas(self) -> pd.Series:
        """Convert to a ``pandas.Series`` indexed by datetime."""
        import pandas as pd

        idx = pd.to_datetime(self.labels, errors="coerce")
        return pd.Series(self.values, index=idx, name="equity")

    def to_dataframe(self) -> pd.DataFrame:
        """Equity + drawdown DataFrame for quick plotting."""
        import pandas as pd

        s = self.to_pandas().astype(float)
        peak = s.cummax()
        drawdown = s - peak
        return pd.DataFrame({"equity": s, "drawdown": drawdown})


class SimulationResult(BaseModel):
    """Result of ``simulate_regular`` / ``simulate_super``.

    ``alpha_id`` is set when ``save=True`` and the simulation succeeded.
    ``overall`` mirrors the dict the web UI displays (sharpe/fitness/etc.).
    """

    model_config = ConfigDict(extra="allow")

    alpha_id: str | None = None
    saved: bool = False
    overall: dict[str, Any] = Field(default_factory=dict)
    pnl: PnLSeries | None = None

    @property
    def sharpe(self) -> float | None:
        v = self.overall.get("sharpe")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def fitness(self) -> float | None:
        v = self.overall.get("fitness")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def turnover(self) -> float | None:
        v = self.overall.get("turnover")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def drawdown(self) -> float | None:
        v = self.overall.get("drawdown")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def passes_overfit(self) -> bool:
        """Quick rule-of-thumb check (Sharpe ≥ 1.58, Fitness ≥ 1.0,
        Turnover 0.01-0.7, |Drawdown| ≤ 0.2). For the authoritative check
        with autocorr-adjusted Ladder Sharpe, fetch via
        ``client.alphas.overfit(self.alpha_id)`` after save."""
        s, f, t, d = self.sharpe, self.fitness, self.turnover, self.drawdown
        if s is None or f is None or t is None or d is None:
            return False
        return (
            s >= 1.58
            and f >= 1.0
            and 0.01 <= t <= 0.7
            and abs(d) <= 0.2
        )
