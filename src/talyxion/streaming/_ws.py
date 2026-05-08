"""Thin sync wrapper around `websockets.sync.client.connect` with typed errors."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from websockets import exceptions as _ws_exc
from websockets.sync.client import ClientConnection, connect

from .._config import Config
from .._version import __version__
from ..errors import (
    TalyxionAuthError,
    TalyxionConnectionError,
    TalyxionResponseError,
)


def _build_url(config: Config, path: str) -> str:
    """Append API key as a query param so the WS handshake can authenticate.

    Backend Channels middleware reads ?api_key=... and resolves it via the
    same `ApiAccessKey` model used by REST. See `main/consumers.py`.
    """
    sep = "&" if "?" in path else "?"
    return f"{config.ws_base_url}{path}{sep}api_key={quote(config.api_key, safe='')}"


def open_ws(config: Config, path: str) -> ClientConnection:
    url = _build_url(config, path)
    headers = [("User-Agent", f"talyxion-python/{__version__}")]
    try:
        return connect(url, additional_headers=headers, open_timeout=config.timeout)
    except _ws_exc.WebSocketException as exc:
        status = _extract_status(exc)
        if status in (401, 403, 4401, 4403):
            raise TalyxionAuthError(
                f"WebSocket auth rejected (status {status}). Check API key and tier.",
                status=status,
            ) from exc
        raise TalyxionConnectionError(f"WebSocket connection failed: {exc}") from exc
    except OSError as exc:
        raise TalyxionConnectionError(f"WebSocket network error: {exc}") from exc


def _extract_status(exc: _ws_exc.WebSocketException) -> int | None:
    """Pull HTTP status off either the new `InvalidStatus` or legacy `InvalidStatusCode`."""
    invalid_status = getattr(_ws_exc, "InvalidStatus", None)
    if invalid_status is not None and isinstance(exc, invalid_status):
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None) if response is not None else None
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None

    invalid_code = getattr(_ws_exc, "InvalidStatusCode", None)
    if invalid_code is not None and isinstance(exc, invalid_code):
        code = getattr(exc, "status_code", None)
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None
    return None


def iter_messages(ws: ClientConnection, *, recv_timeout: float | None = None) -> Iterator[dict[str, Any]]:
    """Yield JSON-decoded messages from the socket until it closes."""
    try:
        while True:
            try:
                raw = ws.recv(timeout=recv_timeout) if recv_timeout else ws.recv()
            except _ws_exc.ConnectionClosed:
                return
            except TimeoutError:
                continue

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                raise TalyxionResponseError(f"Non-JSON WebSocket frame: {raw[:120]!r}") from exc
    finally:
        try:
            ws.close()
        except Exception:
            pass
