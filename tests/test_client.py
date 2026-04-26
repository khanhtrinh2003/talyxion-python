from __future__ import annotations

import httpx
import pytest
import respx

from talyxion import Talyxion, TalyxionAuthError


def _ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("TALYXION_API_KEY", raising=False)
    with pytest.raises(TalyxionAuthError):
        Talyxion()


def test_client_reads_env_key(monkeypatch):
    monkeypatch.setenv("TALYXION_API_KEY", "env_key_xyz")
    c = Talyxion(base_url="https://api.test.talyxion.com")
    assert c.config.api_key == "env_key_xyz"


def test_client_sends_bearer_header(client, base_url, api_key):
    with respx.mock(base_url=base_url) as router:
        route = router.get("/api/v1/status/").mock(
            return_value=_ok(
                {
                    "data": {
                        "status": "ok",
                        "key_name": "test",
                        "key_prefix": "tk_test",
                        "tier": "api",
                        "scopes": ["all"],
                        "daily_quota": 10000,
                        "requests_today": 7,
                        "ip_whitelist_active": False,
                    },
                    "meta": {"timestamp": "2026-04-27T00:00:00Z", "request_id": "abc"},
                }
            )
        )
        status = client.status()
    assert status.tier == "api"
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {api_key}"
    assert "talyxion-python/" in route.calls.last.request.headers["User-Agent"]


def test_context_manager_closes(base_url):
    with Talyxion(api_key="tk_x", base_url=base_url) as c:
        assert c.config.base_url == base_url


def test_ws_base_url_translation():
    c = Talyxion(api_key="tk_x", base_url="https://api.example.com")
    assert c.config.ws_base_url == "wss://api.example.com"
    c2 = Talyxion(api_key="tk_x", base_url="http://localhost:8000")
    assert c2.config.ws_base_url == "ws://localhost:8000"


def test_default_base_url(monkeypatch):
    monkeypatch.delenv("TALYXION_BASE_URL", raising=False)
    c = Talyxion(api_key="tk_x")
    assert c.config.base_url == "https://api.talyxion.com"
    assert c.config.ws_base_url == "wss://api.talyxion.com"
