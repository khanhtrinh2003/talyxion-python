from __future__ import annotations

import httpx
import pytest
import respx

from talyxion import (
    TalyxionAuthError,
    TalyxionNotFoundError,
    TalyxionPermissionError,
    TalyxionRateLimitError,
    TalyxionServerError,
    TalyxionTierError,
)


def test_401_raises_auth_error(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/status/").mock(
            return_value=httpx.Response(
                401, json={"error": "invalid_api_key", "message": "Invalid or revoked API key."}
            )
        )
        with pytest.raises(TalyxionAuthError) as exc:
            client.status()
    assert exc.value.status == 401
    assert exc.value.code == "invalid_api_key"


def test_402_raises_tier_error(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/status/").mock(
            return_value=httpx.Response(
                402,
                json={
                    "error": "tier_insufficient",
                    "message": "Requires 'api' tier or higher.",
                    "required_tier": "api",
                    "current_tier": "pro_plus",
                },
            )
        )
        with pytest.raises(TalyxionTierError) as exc:
            client.status()
    assert exc.value.required_tier == "api"
    assert exc.value.current_tier == "pro_plus"


def test_403_scope_denied(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/status/").mock(
            return_value=httpx.Response(403, json={"error": "scope_denied", "message": "no scope"})
        )
        with pytest.raises(TalyxionPermissionError):
            client.status()


def test_404_not_found(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/ticker/NOPE/").mock(
            return_value=httpx.Response(404, json={"error": "not_found", "message": "no ticker"})
        )
        with pytest.raises(TalyxionNotFoundError):
            client.ticker("nope").info()


def test_429_rate_limit_with_retry_after(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/status/").mock(
            return_value=httpx.Response(
                429,
                headers={"Retry-After": "42"},
                json={"error": "rate_limit_exceeded", "message": "slow down"},
            )
        )
        with pytest.raises(TalyxionRateLimitError) as exc:
            client.status()
    assert exc.value.retry_after == 42


def test_500_raises_server_error(base_url, api_key):
    from talyxion import Talyxion

    c = Talyxion(api_key=api_key, base_url=base_url, max_retries=0, backoff_base=0)
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/status/").mock(
            return_value=httpx.Response(500, json={"error": "internal_error", "message": "boom"})
        )
        with pytest.raises(TalyxionServerError):
            c.status()


def test_5xx_retries_then_succeeds(base_url, api_key):
    from talyxion import Talyxion

    c = Talyxion(api_key=api_key, base_url=base_url, max_retries=2, backoff_base=0)
    payload = {
        "data": {
            "status": "ok",
            "key_name": "x",
            "key_prefix": "tk_x",
            "tier": "api",
            "scopes": ["all"],
            "daily_quota": 1,
            "requests_today": 0,
            "ip_whitelist_active": False,
        },
        "meta": {},
    }
    with respx.mock(base_url=base_url) as router:
        route = router.get("/api/v1/status/").mock(
            side_effect=[
                httpx.Response(503, json={"error": "internal_error", "message": "x"}),
                httpx.Response(200, json=payload),
            ]
        )
        s = c.status()
    assert s.status == "ok"
    assert route.call_count == 2
