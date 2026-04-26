from __future__ import annotations

import httpx
import respx


def test_screener(client, base_url):
    payload = {
        "data": [
            {
                "ticker": "BTC",
                "asset_class": "crypto",
                "side": "long",
                "conviction": 0.91,
                "entry_price": 65000,
                "target_price": 72000,
                "stop_price": 62000,
                "rationale": "regime shift",
            }
        ],
        "pagination": {"total": 1, "limit": 50, "offset": 0},
        "meta": {"timestamp": "...", "request_id": "r"},
    }
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/screener/").mock(return_value=httpx.Response(200, json=payload))
        page = client.screener.run(asset_class="crypto", min_conviction=0.8)
    assert page[0].ticker == "BTC"
    assert page[0].conviction == 0.91


def test_datafields_list(client, base_url):
    payload = {
        "data": [
            {"key": "pe_ratio", "label": "P/E", "category": "valuation", "min_tier": "pro"},
            {"key": "altman_z", "label": "Altman Z", "category": "risk", "min_tier": "pro_plus"},
        ],
        "meta": {"timestamp": "...", "request_id": "r"},
    }
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/datafields/").mock(return_value=httpx.Response(200, json=payload))
        items = client.datafields.list()
    assert {it.key for it in items} == {"pe_ratio", "altman_z"}


def test_datafield_detail(client, base_url):
    payload = {
        "data": {"series": [{"date": "2026-04-26", "value": 12.3}]},
        "field": {"key": "pe_ratio", "label": "P/E"},
        "meta": {"timestamp": "...", "request_id": "r"},
    }
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/datafields/pe_ratio/").mock(return_value=httpx.Response(200, json=payload))
        detail = client.datafields.get("pe_ratio")
    assert detail.label == "P/E"
    assert detail.data["series"][0]["value"] == 12.3


def test_ticker_info_uppercases(client, base_url):
    payload = {
        "data": {
            "ticker": "VIC",
            "latest_signal": {
                "date": "2026-04-27",
                "side": "long",
                "conviction": 0.8,
                "entry_price": 45.5,
                "target_price": 50.0,
                "stop_price": 43.0,
                "rationale": "test",
            },
            "stats": {"signals_30d": 12, "win_rate": 66.7, "avg_conviction": 0.74},
        },
        "meta": {"timestamp": "...", "request_id": "r"},
    }
    with respx.mock(base_url=base_url) as router:
        route = router.get("/api/v1/ticker/VIC/").mock(return_value=httpx.Response(200, json=payload))
        info = client.ticker("vic").info()
    assert route.called
    assert info.ticker == "VIC"
    assert info.stats.win_rate == 66.7
    assert info.latest_signal.entry_price == 45.5


def test_rates_snapshot(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/rates-terminal/data/").mock(
            return_value=httpx.Response(200, json={"data": {"DGS10": 4.21}, "meta": {}})
        )
        snap = client.rates.snapshot()
    assert snap.data["DGS10"] == 4.21


def test_rates_series_with_chart(client, base_url):
    with respx.mock(base_url=base_url) as router:
        route = router.get("/api/v1/rates-terminal/series/").mock(
            return_value=httpx.Response(200, json={"data": [[1, 2]], "meta": {}})
        )
        series = client.rates.series("DGS10", chart=True)
    assert series.id == "DGS10"
    assert route.calls.last.request.url.params["chart"] == "true"


def test_rates_yahoo(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/rates-terminal/yahoo/").mock(
            return_value=httpx.Response(200, json={"data": {"price": 100}, "meta": {}})
        )
        q = client.rates.yahoo("AAPL")
    assert q.symbol == "AAPL"
    assert q.data["price"] == 100


def test_simulations_get(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/simulations/abc-123/").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"status": "running", "progress": 42.0, "message": "step 4/10"}, "meta": {}},
            )
        )
        s = client.simulations.get("abc-123")
    assert s.task_id == "abc-123"
    assert s.status == "running"
    assert s.progress == 42.0
    assert not s.is_terminal


def test_status_endpoint(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/status/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "status": "ok",
                        "key_name": "demo",
                        "key_prefix": "tk_dem",
                        "tier": "institutional",
                        "scopes": ["signals", "datafields"],
                        "daily_quota": 50000,
                        "requests_today": 1234,
                        "ip_whitelist_active": True,
                    },
                    "meta": {},
                },
            )
        )
        s = client.status()
    assert s.tier == "institutional"
    assert s.ip_whitelist_active is True
