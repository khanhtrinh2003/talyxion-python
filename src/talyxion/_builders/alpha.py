"""Fluent builder for alpha simulation.

```python
from talyxion import Talyxion, Backtest

tlx = Talyxion()

# Regular alpha
result = (
    Backtest(region="crypto_trade", universe="TOP19", decay=4, truncation=0.08)
    .alpha("rank(close - ts_mean(close, 20)) * volume", delay=1)
    .simulate(tlx)
)
print(result.alpha_id, result.sharpe, result.passes_overfit())

# Super alpha (combo of Regular alpha ids)
super_result = (
    Backtest(region="crypto_trade", universe="TOP19")
    .super_alpha(["aPE6COnE", "TWs0pveY"], combo="0.6 * a + 0.4 * b")
    .simulate(tlx)
)
```
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..client import Talyxion
    from ..models.alphas import SimulationResult


class Backtest:
    """Fluent builder for alpha simulation (regular or super).

    Common config goes in the constructor; pick the mode with ``.alpha(...)``
    or ``.super_alpha(...)``; terminate with ``.simulate(client)``.
    """

    __slots__ = (
        "_region", "_universe", "_decay", "_truncation", "_neutralization", "_save",
        "_mode", "_code", "_delay", "_long_only",
        "_selections", "_combo", "_limit_selection",
    )

    def __init__(
        self,
        region: str = "crypto_trade",
        universe: str = "TOP19",
        decay: int = 0,
        truncation: float = 0.0,
        neutralization: str = "NONE",
        save: bool = True,
    ) -> None:
        self._region = region
        self._universe = universe
        self._decay = int(decay)
        self._truncation = float(truncation)
        self._neutralization = neutralization
        self._save = bool(save)

        self._mode: Literal["regular", "super"] | None = None
        # Regular-only
        self._code: str = ""
        self._delay: int = 1
        self._long_only: bool = False
        # Super-only
        self._selections: list[str] = []
        self._combo: str = ""
        self._limit_selection: int = 10

    def alpha(self, code: str, *, delay: int = 1, long_only: bool = False) -> Backtest:
        """Configure as a Regular alpha simulation."""
        if self._mode is not None:
            raise ValueError(f"Backtest mode already set to {self._mode!r}.")
        self._mode = "regular"
        self._code = code
        self._delay = int(delay)
        self._long_only = bool(long_only)
        return self

    def super_alpha(
        self,
        selections: list[str],
        *,
        combo: str,
        limit_selection: int = 10,
    ) -> Backtest:
        """Configure as a Super alpha simulation (linear combo of Regular alpha ids)."""
        if self._mode is not None:
            raise ValueError(f"Backtest mode already set to {self._mode!r}.")
        if not selections:
            raise ValueError("super_alpha requires at least one selection.")
        self._mode = "super"
        self._selections = list(selections)
        self._combo = combo
        self._limit_selection = int(limit_selection)
        return self

    def simulate(self, client: Talyxion) -> SimulationResult:
        if self._mode == "regular":
            return client.alphas.simulate_regular(
                self._code,
                region=self._region,
                universe=self._universe,
                delay=self._delay,
                decay=self._decay,
                truncation=self._truncation,
                neutralization=self._neutralization,
                long_only=self._long_only,
                save=self._save,
            )
        if self._mode == "super":
            return client.alphas.simulate_super(
                self._selections,
                self._combo,
                region=self._region,
                universe=self._universe,
                decay=self._decay,
                truncation=self._truncation,
                neutralization=self._neutralization,
                limit_selection=self._limit_selection,
                save=self._save,
            )
        raise ValueError("Backtest mode not set — call .alpha(...) or .super_alpha(...) first.")

    def __repr__(self) -> str:
        if self._mode == "regular":
            return f"<Backtest regular code={self._code[:40]!r}… region={self._region} universe={self._universe}>"
        if self._mode == "super":
            return f"<Backtest super n_selections={len(self._selections)} combo={self._combo[:40]!r}…>"
        return f"<Backtest unset region={self._region} universe={self._universe}>"
