"""Internal httpx-based transport with retry, auth, and error mapping."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

import httpx

from ._config import Config
from ._version import __version__
from .errors import (
    TalyxionConnectionError,
    TalyxionResponseError,
    from_response,
)


class HttpClient:
    def __init__(self, config: Config, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "User-Agent": f"talyxion-python/{__version__}",
            "Accept": "application/json",
        }
        self._client = httpx.Client(
            base_url=config.base_url,
            headers=headers,
            timeout=config.timeout,
            transport=transport,
        )

    @property
    def config(self) -> Config:
        return self._config

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=_clean_params(params))

    def post(self, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, json=json)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= self._config.max_retries:
                    raise TalyxionConnectionError(f"Request timed out after {attempt + 1} attempts: {exc}") from exc
                self._sleep_backoff(attempt)
                continue
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= self._config.max_retries:
                    raise TalyxionConnectionError(f"Network error after {attempt + 1} attempts: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            if 500 <= response.status_code < 600 and attempt < self._config.max_retries:
                self._sleep_backoff(attempt)
                continue

            return self._handle_response(response)

        raise TalyxionConnectionError(f"Exhausted retries: {last_exc}")

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        body = self._safe_json(response)
        request_id = None
        if isinstance(body, dict):
            meta = body.get("meta")
            if isinstance(meta, dict):
                rid = meta.get("request_id")
                if isinstance(rid, str):
                    request_id = rid

        if response.status_code >= 400:
            err_body = body if isinstance(body, dict) else {"detail": str(body)}
            if response.status_code == 429:
                ra = response.headers.get("Retry-After")
                if ra and "retry_after" not in err_body:
                    try:
                        err_body["retry_after"] = int(ra)
                    except ValueError:
                        pass
            raise from_response(response.status_code, err_body, request_id=request_id)

        if not isinstance(body, dict):
            raise TalyxionResponseError(
                f"Expected JSON object from {response.request.url}, got {type(body).__name__}",
                status=response.status_code,
                request_id=request_id,
            )
        return body

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._config.backoff_base * (2**attempt)
        time.sleep(delay)


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    return {k: v for k, v in params.items() if v is not None}
