"""Stream feed-wide events (comments, posts, presence)."""

from __future__ import annotations

from collections.abc import Iterator

from .._config import Config
from ..models.simulation import FeedEvent
from ._ws import iter_messages, open_ws


class FeedEventsStream:
    def __init__(self, config: Config) -> None:
        self._config = config

    def __call__(self, *, recv_timeout: float | None = None) -> Iterator[FeedEvent]:
        ws = open_ws(self._config, "/ws/feed-events/")
        for msg in iter_messages(ws, recv_timeout=recv_timeout):
            event_type = str(msg.pop("type", "unknown"))
            yield FeedEvent(type=event_type, payload=msg)
