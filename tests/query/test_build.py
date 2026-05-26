"""Query (build) feature tests.

The taxonomy-build endpoint is part of the query feature because A2X
search depends on a built taxonomy. Build behavior:

- POST /api/datasets/{ds}/build         — heavy: 503 in lite
- GET  /api/datasets/{ds}/build/status  — light: works in both modes
"""

from __future__ import annotations


def test_build_trigger_returns_503(lite_app, dataset):
    r = lite_app.post(
        f"/api/datasets/{dataset}/build", json={"resume": "no"}
    )
    assert r.status_code == 503
    assert r.json()["extras"] == "vector"


def test_build_status_works_in_lite(lite_app, dataset):
    """Build status reads ``_build_jobs`` dict; no extras needed."""
    r = lite_app.get(f"/api/datasets/{dataset}/build/status")
    assert r.status_code == 200
    assert r.json()["status"] == "idle"


def test_build_status_route_full(full_app):
    full_app.post("/api/datasets", json={"name": "full_status"})
    r = full_app.get("/api/datasets/full_status/build/status")
    assert r.status_code == 200
    assert r.json()["status"] == "idle"
