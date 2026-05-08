"""`/api/v1/simulations/<task_id>/`."""

from __future__ import annotations

import time
from typing import Any

from ..errors import TalyxionError
from ..models.simulation import SimulationStatus
from ._base import Resource


class SimulationsResource(Resource):
    def get(self, task_id: str) -> SimulationStatus:
        body = self._http.get(f"/api/v1/simulations/{task_id}/")
        # Backend wraps either as `{"data": {...}}` or returns the AsyncResult dict directly.
        raw = body.get("data")
        payload: dict[str, Any] = raw if isinstance(raw, dict) else body
        merged: dict[str, Any] = {**payload, "task_id": task_id}
        return SimulationStatus.model_validate(merged)

    def wait(
        self,
        task_id: str,
        *,
        timeout: float = 300.0,
        poll: float = 2.0,
    ) -> SimulationStatus:
        """Poll until the simulation finishes or `timeout` elapses."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.get(task_id)
            if status.is_terminal:
                return status
            if time.monotonic() >= deadline:
                raise TalyxionError(
                    f"Simulation {task_id} did not finish within {timeout:.0f}s "
                    f"(last status={status.status}, progress={status.progress}).",
                    code="simulation_timeout",
                )
            time.sleep(poll)
