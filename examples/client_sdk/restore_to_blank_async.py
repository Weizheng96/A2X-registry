"""Asynchronous examples for ``AsyncA2XClient.restore_to_blank``.

Mirrors ``restore_to_blank_sync.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import (
    AsyncA2XClient,
    A2XClient,  # for the bypass-ownership second client
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ValidationError,
)


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_restore_to_blank_async.json"

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_restore_to_blank_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        # Lifecycle blank → team → restore
        reg = await client.register_blank_agent(ds, endpoint="http://teammate:8080")
        sid = reg.service_id
        await client.replace_agent_card(ds, sid, {
            "name": "Worker", "description": "组队中",
            "endpoint": "http://teammate:8080", "status": "busy",
        })

        print("\n[L1 cache hit — no extra GET]")
        await client.restore_to_blank(ds, sid)
        d = await client.get_agent(ds, sid)
        print(f"  description: {d.metadata['description']!r}")

        print("\n[L2 fallback — clear cache, restore reads endpoint]")
        await client.replace_agent_card(ds, sid, {
            "name": "Worker", "description": "again",
            "endpoint": "http://teammate:8080", "status": "busy",
        })
        client._blank_endpoints.clear()
        await client.restore_to_blank(ds, sid)
        print("  restored")

        print("\n[L3 — card lacks endpoint → ValueError]")
        await client.register_agent(ds, {"protocolVersion": "0.0",
                                         "name": "BareAgent",
                                         "description": "no endpoint"},
                                    service_id="agent_no_endpoint")
        client._blank_endpoints.clear()
        try:
            await client.restore_to_blank(ds, "agent_no_endpoint")
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}")

        print("\n[foreign sid → NotOwnedError]")
        try:
            await client.restore_to_blank(ds, "never_owned")
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")

        print("\n[backend deleted → NotFoundError]")
        with A2XClient(base_url=base_url, ownership_file=False) as other:
            other._owned.add(ds, sid)
            other.deregister_agent(ds, sid)
        try:
            await client.restore_to_blank(ds, sid)
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__}")

        await client.delete_dataset(ds)

    print("\n[network failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999",
                                  ownership_file=False, timeout=2.0) as bad_client:
            bad_client._owned.add("ds", "sid")
            bad_client._blank_endpoints[("ds", "sid")] = "http://x"
            await bad_client.restore_to_blank("ds", "sid")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
