"""Aggregator exposed as `client.stream`."""

from __future__ import annotations

from collections.abc import Iterator

from .._config import Config
from ..models.simulation import FeedEvent, SimulationProgressEvent
from .feed_events import FeedEventsStream
from .sim_progress import SimProgressStream


class Stream:
    def __init__(self, config: Config) -> None:
        self._sim = SimProgressStream(config)
        self._feed = FeedEventsStream(config)

    def sim_progress(self, task_id: str, *, recv_timeout: float | None = None) -> Iterator[SimulationProgressEvent]:
        return self._sim(task_id, recv_timeout=recv_timeout)

    def feed_events(self, *, recv_timeout: float | None = None) -> Iterator[FeedEvent]:
        return self._feed(recv_timeout=recv_timeout)
