"""Exception hierarchy for the Talyxion SDK.

All HTTP errors map to a `TalyxionError` subclass. The mapping mirrors the
backend error contract defined in `main/api_auth.py::_error()`.
"""

from __future__ import annotations

from typing import Any


class TalyxionError(Exception):
    """Base class for all SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.request_id = request_id
        self.payload: dict[str, Any] = payload or {}

    def __str__(self) -> str:
        parts = [self.message]
        if self.code:
            parts.append(f"code={self.code}")
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " | ".join(parts)


class TalyxionAuthError(TalyxionError):
    """Missing, invalid, or revoked API key (HTTP 401)."""


class TalyxionTierError(TalyxionError):
    """Subscription tier insufficient for the requested endpoint (HTTP 402)."""

    def __init__(self, message: str, *, required_tier: str | None = None, current_tier: str | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.required_tier = required_tier
        self.current_tier = current_tier


class TalyxionPermissionError(TalyxionError):
    """Scope or IP whitelist denial (HTTP 403)."""


class TalyxionNotFoundError(TalyxionError):
    """Requested resource does not exist (HTTP 404)."""


class TalyxionRateLimitError(TalyxionError):
    """IP rate limit or per-key daily quota exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: int | None = None,
        quota: int | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after
        self.quota = quota


class TalyxionBadRequestError(TalyxionError):
    """Client supplied invalid parameters (HTTP 400)."""


class TalyxionServerError(TalyxionError):
    """Backend returned a 5xx response."""


class TalyxionResponseError(TalyxionError):
    """Response body could not be parsed or did not match expected schema."""


class TalyxionConnectionError(TalyxionError):
    """Network-level failure (DNS, TCP, TLS, timeout) after retries."""


_CODE_MAP: dict[str, type[TalyxionError]] = {
    "authentication_required": TalyxionAuthError,
    "invalid_api_key": TalyxionAuthError,
    "key_expired": TalyxionAuthError,
    "no_user": TalyxionAuthError,
    "tier_insufficient": TalyxionTierError,
    "ip_not_allowed": TalyxionPermissionError,
    "scope_denied": TalyxionPermissionError,
    "not_found": TalyxionNotFoundError,
    "rate_limit_exceeded": TalyxionRateLimitError,
    "daily_quota_exceeded": TalyxionRateLimitError,
    "internal_error": TalyxionServerError,
}


def from_response(status: int, body: dict[str, Any] | None, request_id: str | None = None) -> TalyxionError:
    """Translate an HTTP status + JSON body into the right exception."""
    body = body or {}
    code = str(body.get("error") or "")
    message = str(body.get("message") or body.get("detail") or f"HTTP {status}")

    cls: type[TalyxionError]
    if code and code in _CODE_MAP:
        cls = _CODE_MAP[code]
    elif status == 400:
        cls = TalyxionBadRequestError
    elif status == 401:
        cls = TalyxionAuthError
    elif status == 402:
        cls = TalyxionTierError
    elif status == 403:
        cls = TalyxionPermissionError
    elif status == 404:
        cls = TalyxionNotFoundError
    elif status == 429:
        cls = TalyxionRateLimitError
    elif 500 <= status < 600:
        cls = TalyxionServerError
    else:
        cls = TalyxionError

    extra: dict[str, Any] = {}
    if cls is TalyxionRateLimitError:
        ra = body.get("retry_after")
        if ra is not None:
            try:
                extra["retry_after"] = int(ra)
            except (TypeError, ValueError):
                pass
        q = body.get("quota")
        if q is not None:
            try:
                extra["quota"] = int(q)
            except (TypeError, ValueError):
                pass
    if cls is TalyxionTierError:
        extra["required_tier"] = body.get("required_tier")
        extra["current_tier"] = body.get("current_tier")

    return cls(message, code=code or None, status=status, request_id=request_id, payload=body, **extra)


__all__ = [
    "TalyxionError",
    "TalyxionAuthError",
    "TalyxionTierError",
    "TalyxionPermissionError",
    "TalyxionNotFoundError",
    "TalyxionRateLimitError",
    "TalyxionBadRequestError",
    "TalyxionServerError",
    "TalyxionResponseError",
    "TalyxionConnectionError",
    "from_response",
]
