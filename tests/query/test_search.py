"""Query (search) feature tests.

Covers the A2X search surface:
- POST /api/search           — sync search (503 in lite, gate-passes in full)
- POST /api/search/judge     — relevance judge (503 in lite)
- WS   /api/search/ws        — streaming search (lite delivers install hint)

In lite mode every search route must respond with the structured
``FeatureNotInstalledError`` body so SDK users get a copy-pasteable
``pip install`` hint instead of a stack trace.
"""

from __future__ import annotations


def test_search_returns_503(lite_app, dataset):
    r = lite_app.post(
        "/api/search",
        json={"query": "x", "method": "vector", "dataset": dataset, "top_k": 3},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["feature"] == "vector"
    assert body["extras"] == "vector"
    assert "pip install 'a2x-registry[vector]'" in body["detail"]


def test_search_judge_returns_503(lite_app):
    r = lite_app.post(
        "/api/search/judge",
        json={"query": "x", "services": []},
    )
    assert r.status_code == 503
    assert r.json()["extras"] == "vector"


def test_search_ws_returns_install_hint(lite_app, dataset):
    """WebSocket: server accepts, then sends an error JSON with the hint."""
    with lite_app.websocket_connect("/api/search/ws") as ws:
        ws.send_json({
            "query": "x", "method": "vector",
            "dataset": dataset, "top_k": 3,
        })
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "pip install 'a2x-registry[vector]'" in msg["message"]


def test_search_route_passes_gate_full(full_app):
    """In full mode the search gate is a no-op and the route is mounted."""
    from a2x_registry.common import feature_flags
    feature_flags.require("vector")  # must not raise in full mode

    paths = [r.path for r in full_app.app.routes if hasattr(r, "path")]
    assert "/api/search" in paths
    assert "/api/search/judge" in paths
