"""Stream alpha simulation progress events."""

from __future__ import annotations

from collections.abc import Iterator

from .._config import Config
from ..models.simulation import SimulationProgressEvent
from ._ws import iter_messages, open_ws


class SimProgressStream:
    def __init__(self, config: Config) -> None:
        self._config = config

    def __call__(self, task_id: str, *, recv_timeout: float | None = None) -> Iterator[SimulationProgressEvent]:
        ws = open_ws(self._config, f"/ws/sim-progress/{task_id}/")
        for msg in iter_messages(ws, recv_timeout=recv_timeout):
            yield SimulationProgressEvent.model_validate(msg)
