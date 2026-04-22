"""Synchronous examples for ``A2XClient.replace_agent_card``.

This file demonstrates:

1. fully replacing an owned agent's card (POST /services/a2a same sid)
2. endpoint auto-fill: omit endpoint → SDK fills from L1 cache
3. L1 cache refreshed after success (next replace can omit endpoint too)
4. local fail-fast on non-dict card (ValueError before HTTP)
5. local fail-fast on foreign sid (NotOwnedError before HTTP)
6. NotFoundError when sid was deleted on the backend
7. network failure

Run:
    python examples/client_sdk/replace_agent_card_sync.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import (
    A2XClient,
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ValidationError,
)


def ensure_absent(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_replace_card_sync.json"

    with A2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_replace_agent_card_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        # Seed: register as blank — populates L1 cache
        reg = client.register_blank_agent(ds, endpoint="http://teammate:8080")
        sid = reg.service_id
        print(f"[seed] registered blank: sid={sid}, L1 cache primed")

        # 1) Replace with full team card (endpoint provided)
        print("\n[explicit endpoint → use as-is]")
        team_card = {
            "name": "Worker", "description": "已加入团队",
            "endpoint": "http://teammate:8080",
            "status": "busy",
            "skills": [{"name": "exec", "description": "执行子任务"}],
        }
        resp = client.replace_agent_card(ds, sid, team_card)
        print(f"  status: {resp.status}")

        # 2) Replace AGAIN with NO endpoint → SDK auto-fills from L1
        print("\n[endpoint omitted → auto-fill from L1 cache, no extra GET]")
        resp = client.replace_agent_card(ds, sid, {
            "name": "Worker v2", "description": "更新版",
            "status": "busy",  # no endpoint key here
        })
        print(f"  status: {resp.status}")
        # Verify backend stored endpoint correctly
        detail = client.get_agent(ds, sid)
        print(f"  backend endpoint preserved: {detail.metadata['endpoint']}")

        # 3) Non-dict card → ValueError
        print("\n[non-dict card → ValueError, no HTTP]")
        for bad in [None, [1, 2], "card", 42]:
            try:
                client.replace_agent_card(ds, sid, bad)
            except ValueError as exc:
                print(f"  card={bad!r:>10}: {type(exc).__name__}")

        # 4) Foreign sid → NotOwnedError (precedes card validation)
        print("\n[foreign sid → NotOwnedError, no HTTP]")
        try:
            client.replace_agent_card(ds, "never_owned", {"name": "x"})
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}: {exc}")

        # 5) NotFoundError + auto-cleanup if sid removed on backend
        print("\n[backend deleted under us → NotFoundError + cache cleanup]")
        # Simulate via a second client bypassing ownership
        with A2XClient(base_url=base_url, ownership_file=False) as other:
            other._owned.add(ds, sid)
            other.deregister_agent(ds, sid)
        try:
            client.replace_agent_card(ds, sid, {
                "name": "ghost", "description": "should not land",
                "endpoint": "http://teammate:8080",
            })
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__} status_code={exc.status_code}")
            print(f"  L1 cache cleaned: {(ds, sid) not in client._blank_endpoints}")
            print(f"  ownership cleaned: {not client._owned.contains(ds, sid)}")

        client.delete_dataset(ds)

    # 6) Network failure
    print("\n[network / gateway failure]")
    try:
        with A2XClient(base_url="http://127.0.0.1:8999",
                       ownership_file=False, timeout=2.0) as bad_client:
            bad_client._owned.add("ds", "sid")
            bad_client.replace_agent_card("ds", "sid",
                                          {"name": "x", "description": "y",
                                           "endpoint": "http://e"})
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    main()
