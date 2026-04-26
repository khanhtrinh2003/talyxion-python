"""Resolved configuration for the Talyxion client."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import TalyxionAuthError

DEFAULT_BASE_URL = "https://api.talyxion.com"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    timeout: float
    max_retries: int
    backoff_base: float

    @classmethod
    def resolve(
        cls,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_base: float | None = None,
    ) -> Config:
        key = api_key or os.environ.get("TALYXION_API_KEY", "").strip()
        if not key:
            raise TalyxionAuthError(
                "Missing API key. Pass api_key=... to Talyxion(...) or set TALYXION_API_KEY in the environment.",
                code="authentication_required",
                status=401,
            )

        url = (base_url or os.environ.get("TALYXION_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        return cls(
            api_key=key,
            base_url=url,
            timeout=float(timeout if timeout is not None else os.environ.get("TALYXION_TIMEOUT", DEFAULT_TIMEOUT)),
            max_retries=int(max_retries if max_retries is not None else os.environ.get("TALYXION_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
            backoff_base=float(backoff_base if backoff_base is not None else DEFAULT_BACKOFF_BASE),
        )

    @property
    def ws_base_url(self) -> str:
        if self.base_url.startswith("https://"):
            return "wss://" + self.base_url[len("https://"):]
        if self.base_url.startswith("http://"):
            return "ws://" + self.base_url[len("http://"):]
        return self.base_url
