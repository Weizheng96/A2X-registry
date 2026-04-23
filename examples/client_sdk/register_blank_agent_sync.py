"""Synchronous examples for ``A2XClient.register_blank_agent``.

This file demonstrates:

1. registering a blank agent (description='__BLANK__', status="online")
2. idempotency: re-registering same endpoint returns status='updated'
3. L1 endpoint cache populated for restore_to_blank reuse
4. local fail-fast on bad endpoint (None / empty / non-string)
5. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/register_blank_agent_sync.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from a2x_client import A2XClient, A2XConnectionError, A2XHTTPError, ValidationError


def ensure_absent(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_register_blank_sync.json"

    print(f"Using backend: {base_url}")

    with A2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_register_blank_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        # 1) Register a blank agent
        print("\n[register blank agent]")
        resp = client.register_blank_agent(ds, endpoint="http://teammate.example:8080")
        print(f"  service_id: {resp.service_id}")
        print(f"  status:     {resp.status}")
        print(f"  L1 cache contains it: {(ds, resp.service_id) in client._blank_endpoints}")

        # 2) Verify the card on the backend
        detail = client.get_agent(ds, resp.service_id)
        print(f"  backend description: {detail.metadata.get('description')!r}")
        print(f"  backend endpoint:    {detail.metadata.get('endpoint')!r}")
        print(f"  backend status:      {detail.metadata.get('status')!r}")

        # 3) Idempotent re-register (same endpoint → same name → same sid)
        print("\n[re-register same endpoint → status='updated']")
        resp2 = client.register_blank_agent(ds, endpoint="http://teammate.example:8080")
        print(f"  service_id: {resp2.service_id}  (same as before: {resp2.service_id == resp.service_id})")
        print(f"  status:     {resp2.status}")

        # 4) Different endpoint → distinct sid
        print("\n[different endpoint → new sid]")
        resp3 = client.register_blank_agent(ds, endpoint="http://teammate.example:8081")
        print(f"  service_id: {resp3.service_id}")
        print(f"  distinct from first? {resp3.service_id != resp.service_id}")

        # 5) Local fail-fast on bad endpoint
        print("\n[bad endpoint → ValueError, no HTTP]")
        for bad in ["", "   ", None, 42]:
            try:
                client.register_blank_agent(ds, endpoint=bad)
            except ValueError as exc:
                print(f"  endpoint={bad!r:>10}: {type(exc).__name__}: {str(exc)[:50]}")

        client.delete_dataset(ds)

    # 6) Network failure
    print("\n[network / gateway failure]")
    try:
        with A2XClient(base_url="http://127.0.0.1:8999",
                       ownership_file=False, timeout=2.0) as bad_client:
            bad_client.register_blank_agent("example_unreachable", endpoint="http://x")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__} status_code={exc.status_code}")


if __name__ == "__main__":
    main()
