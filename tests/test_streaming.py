from __future__ import annotations

import json
import threading

import pytest
from websockets.sync.server import serve

from talyxion import Talyxion


@pytest.fixture()
def ws_server():
    """Echo a scripted sequence of frames to any client and close."""
    scripts: dict[str, list[dict]] = {}

    def handler(ws):
        path = getattr(ws.request, "path", "/")
        # path includes query string with api_key
        base = path.split("?", 1)[0]
        for frame in scripts.get(base, []):
            ws.send(json.dumps(frame))
        ws.close()

    server = serve(handler, "127.0.0.1", 0)
    host, port = server.socket.getsockname()[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "scripts": scripts,
            "ws_url": f"ws://{host}:{port}",
            "http_url": f"http://{host}:{port}",
        }
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _client(ws_server):
    return Talyxion(
        api_key="tk_test",
        base_url=ws_server["http_url"],
        max_retries=0,
        backoff_base=0,
    )


def test_sim_progress_yields_events(ws_server):
    ws_server["scripts"]["/ws/sim-progress/task-1/"] = [
        {"status": "running", "progress": 10, "message": "step 1"},
        {"status": "running", "progress": 50, "message": "step 5"},
        {"status": "done", "progress": 100, "message": "ok", "data": {"sharpe": 1.4}},
    ]

    client = _client(ws_server)
    events = list(client.stream.sim_progress("task-1"))
    assert [e.progress for e in events] == [10, 50, 100]
    assert events[-1].status == "done"
    assert events[-1].data == {"sharpe": 1.4}


def test_feed_events_yields_typed_events(ws_server):
    ws_server["scripts"]["/ws/feed-events/"] = [
        {"type": "comment_created", "post_id": 7, "comment_count": 4},
        {"type": "post_published", "post_id": 8},
    ]

    client = _client(ws_server)
    events = list(client.stream.feed_events())
    assert [e.type for e in events] == ["comment_created", "post_published"]
    assert events[0].payload == {"post_id": 7, "comment_count": 4}
