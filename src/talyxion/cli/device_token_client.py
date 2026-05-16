"""Thin HTTP client that attaches the device token to every request.

Used by the CLI for every API call against ``/api/v1/talyxion/...``.
Reads the base URL from ``TALYXION_BASE_URL`` env var (default
``https://talyxion.com``), reads the token from the OS keyring on
construction.

Raises:
  ``NotAuthenticatedError`` — no token in keyring (user hasn't run
    ``talyxion auth login`` yet).
  ``TokenRevokedError`` — server returned 401 ``key_expired`` /
    ``invalid_device_token``. CLI clears keyring and prompts re-login.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from talyxion.cli._version import __cli_version__
from talyxion.cli.keyring_store import (
    delete_device_token,
    load_device_token,
)

DEFAULT_BASE = "https://talyxion.com"


class NotAuthenticatedError(RuntimeError):
    pass


class TokenRevokedError(RuntimeError):
    pass


def base_url() -> str:
    return os.environ.get("TALYXION_BASE_URL", DEFAULT_BASE).rstrip("/")


def _api_prefix() -> str:
    """Talyxion API endpoints live under ``/api/v1/talyxion/`` (see vnweb/urls.py)."""
    return f"{base_url()}/api/v1/talyxion"


class DeviceTokenClient:
    """HTTP client bound to the user's device token.

    Use as a context manager so the underlying ``httpx.Client`` is closed
    cleanly even if a daemon loop crashes::

        with DeviceTokenClient() as cli:
            who = cli.get("/trading/whoami/")
    """

    def __init__(self, *, base: str | None = None, timeout: float = 30.0):
        token = load_device_token()
        if not token:
            raise NotAuthenticatedError(
                "No device token in keyring. Run `talyxion auth login` first."
            )
        self._token = token
        self._base = (base or _api_prefix()).rstrip("/")
        self._http = httpx.Client(
            base_url=self._base,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "X-App-Version": __cli_version__,
                # Browser-like UA envelope to avoid Cloudflare bot-fight WAF;
                # the talyxion-cli build identifier still goes in for our
                # own analytics. Server reads X-App-Version separately.
                "User-Agent": (
                    f"Mozilla/5.0 (compatible; talyxion-cli/{__cli_version__}; "
                    "+https://talyxion.com/platform/trading/setup/)"
                ),
                "Accept": "application/json",
            },
        )

    # ── lifecycle ────────────────────────────────────────────────
    def __enter__(self) -> DeviceTokenClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ── HTTP helpers ─────────────────────────────────────────────
    def _check_revoked(self, resp: httpx.Response) -> None:
        if resp.status_code == 401:
            try:
                body = resp.json()
            except Exception:
                body = {}
            err = body.get("error", "")
            if err in {"invalid_device_token", "key_expired", "authentication_required"}:
                delete_device_token()
                raise TokenRevokedError(
                    body.get("message")
                    or "Device token revoked. Run `talyxion auth login` to re-pair."
                )

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._http.get(path, **kwargs)
        self._check_revoked(resp)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, json: Any = None, **kwargs: Any) -> dict[str, Any]:
        resp = self._http.post(path, json=json, **kwargs)
        self._check_revoked(resp)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()

    def delete(self, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._http.delete(path, **kwargs)
        self._check_revoked(resp)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()


class FriendlyHTTPError(RuntimeError):
    """Raised by visibility commands when an HTTP call fails.

    Carries both a short headline and a longer hint so the REPL can
    render a panel, while ``/doctor`` (or any other caller that wants
    to summarise) can grab just the headline.
    """

    def __init__(self, headline: str, hint: str | None) -> None:
        super().__init__(headline)
        self.headline = headline
        self.hint = hint


def explain_http_failure(exc: Exception, path: str) -> tuple[str, str | None]:
    """Translate an httpx exception into (headline, hint) for the user.

    ``hint`` may be None when the headline says everything. The cli's
    visibility commands (``/balance``, ``/positions``, ``/portfolio``,
    ``/profiles``, ``/show``) call this so a generic "Request failed:
    404 Not Found" turns into something the user can actually act on.

    The hint specifically distinguishes the case where the server is
    *up* (auth works, ``whoami`` works) but a specific endpoint isn't
    available — that's almost always a CLI-newer-than-server skew, and
    naming the cause saves the user from filing a "broken" bug.
    """
    # ── Network-layer errors ────────────────────────────────────────
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return (
            f"Can't reach the server at {base_url()}.",
            "Check your internet connection, VPN, or the TALYXION_BASE_URL "
            "env var if you're pointing at a non-default server.",
        )
    if isinstance(exc, httpx.ReadTimeout):
        return (
            f"Server didn't respond in time for {path}.",
            "Could be a temporary blip — retry the command. If it persists, "
            "type /doctor to ping the server.",
        )

    # ── HTTP-layer errors ───────────────────────────────────────────
    if not isinstance(exc, httpx.HTTPStatusError):
        return (str(exc), None)

    resp = exc.response
    code = resp.status_code
    try:
        body = resp.json()
    except Exception:
        body = None
    server_msg = ""
    if isinstance(body, dict):
        # Talyxion's API returns ``{"error":"...", "message":"..."}`` or
        # Django REST's ``{"detail":"..."}`` depending on the endpoint.
        server_msg = (
            body.get("message")
            or body.get("detail")
            or body.get("error")
            or ""
        )

    if code == 404:
        return (
            f"Server doesn't expose {path}  (HTTP 404).",
            "Your CLI is likely newer than the server deployment — the "
            "endpoint exists locally but hasn't shipped to talyxion.com "
            "yet. Type /doctor to check version skew, or wait for the "
            "next server deploy. If you're pointing at a self-hosted "
            "instance via TALYXION_BASE_URL, update that server first."
            + (f"\n\nServer said: {server_msg}" if server_msg else ""),
        )
    if code == 403:
        return (
            f"Server refused {path}  (HTTP 403).",
            (server_msg or "Your token's scope or your account tier doesn't "
                          "allow this operation.")
            + "  Type /tier to check what your subscription covers.",
        )
    if code == 429:
        return (
            f"Rate-limited by the server  (HTTP 429).",
            f"Back off for a few seconds and retry. {server_msg}".strip(),
        )
    if 500 <= code < 600:
        return (
            f"Server error  (HTTP {code}) on {path}.",
            (server_msg or "Talyxion's server hit an internal error. "
                          "Try again in a moment.")
            + "  Type /doctor to confirm the server is reachable.",
        )
    # Generic 4xx fallback
    return (
        f"Request rejected  (HTTP {code}) on {path}.",
        server_msg or "Check the command arguments and try /help <command>.",
    )
