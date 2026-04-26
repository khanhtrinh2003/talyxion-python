from __future__ import annotations

import httpx
import respx

SIGNAL_ITEM = {
    "date": "2026-04-27",
    "ticker": "VIC",
    "asset_class": "vn_equity",
    "side": "long",
    "conviction": 0.82,
    "entry_price": 45.5,
    "target_price": 50.0,
    "stop_price": 43.0,
    "expected_return_pct": 9.9,
    "source": "alpha-1",
    "rationale": "breakout",
    "outcome": None,
}


def _page(items, total=10, limit=2, offset=0):
    return {
        "data": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
        "meta": {"timestamp": "2026-04-27T00:00:00Z", "request_id": "rid"},
    }


def test_signals_list_parses_items(client, base_url):
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/signals/").mock(
            return_value=httpx.Response(200, json=_page([SIGNAL_ITEM, SIGNAL_ITEM]))
        )
        page = client.signals.list(date="2026-04-27", asset_class="vn_equity")
    assert len(page) == 2
    assert page[0].ticker == "VIC"
    assert page[0].conviction == 0.82
    assert page.pagination.total == 10


def test_signals_list_passes_query_params(client, base_url):
    with respx.mock(base_url=base_url) as router:
        route = router.get("/api/v1/signals/").mock(
            return_value=httpx.Response(200, json=_page([]))
        )
        client.signals.list(
            date="2026-04-27",
            asset_class="crypto",
            side="long",
            min_conviction=0.7,
            limit=25,
        )
    sent = route.calls.last.request.url.params
    assert sent["date"] == "2026-04-27"
    assert sent["asset_class"] == "crypto"
    assert sent["side"] == "long"
    assert sent["min_conviction"] == "0.7"
    assert sent["limit"] == "25"


def test_signals_list_iter_all_paginates(client, base_url):
    page1 = _page([SIGNAL_ITEM], total=2, limit=1, offset=0)
    page2 = _page([{**SIGNAL_ITEM, "ticker": "HPG"}], total=2, limit=1, offset=1)

    with respx.mock(base_url=base_url) as router:
        route = router.get("/api/v1/signals/").mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        first = client.signals.list(limit=1)
        symbols = [s.ticker for s in first.iter_all()]
    assert symbols == ["VIC", "HPG"]
    assert route.call_count == 2


def test_signals_history(client, base_url):
    payload = {
        "data": [
            {
                "date": "2026-04-26",
                "side": "long",
                "conviction": 0.7,
                "entry_price": 100,
                "outcome": "win",
                "realized_return_pct": 4.2,
            }
        ],
        "ticker": "HPG",
        "pagination": {"total": 1, "limit": 50, "offset": 0},
        "meta": {"timestamp": "...", "request_id": "r"},
    }
    with respx.mock(base_url=base_url) as router:
        router.get("/api/v1/signals/history/").mock(return_value=httpx.Response(200, json=payload))
        page = client.signals.history("HPG", days=7)
    assert page[0].outcome == "win"
    assert page[0].realized_return_pct == 4.2
