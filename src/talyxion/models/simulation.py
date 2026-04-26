"""Simulation status (matches `SimulationTaskViewSet` and Channels payloads)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SimStatusLiteral = Literal["queued", "running", "done", "error"]


class SimulationStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str | None = None
    status: SimStatusLiteral = "queued"
    progress: float = 0.0
    message: str | None = None
    data: Any = None
    result: Any = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("done", "error")


class SimulationProgressEvent(BaseModel):
    """Event yielded by `client.stream.sim_progress(task_id)`."""

    model_config = ConfigDict(extra="allow")

    status: SimStatusLiteral = "queued"
    progress: float = 0.0
    message: str | None = None
    data: Any = None


class FeedEvent(BaseModel):
    """Event yielded by `client.stream.feed_events()`."""

    model_config = ConfigDict(extra="allow")

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
