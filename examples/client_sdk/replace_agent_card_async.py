"""Asynchronous examples for ``AsyncA2XRegistryClient.replace_agent_card``.

Mirrors ``replace_agent_card_sync.py``.
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

from a2x_registry_client import (
    AsyncA2XRegistryClient,
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ValidationError,
    A2XRegistryClient,  # for the bypass-ownership second client
)


async def ensure_absent(client: AsyncA2XRegistryClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_replace_card_async.json"

    async with AsyncA2XRegistryClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_replace_agent_card_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        reg = await client.register_blank_agent(ds, endpoint="http://teammate:8080")
        sid = reg.service_id
        print(f"[seed] sid={sid}")

        print("\n[explicit endpoint]")
        await client.replace_agent_card(ds, sid, {
            "name": "Worker", "description": "team",
            "endpoint": "http://teammate:8080", "status": "busy",
        })
        print("  ok")

        print("\n[endpoint omitted → auto-fill]")
        await client.replace_agent_card(ds, sid, {
            "name": "Worker v2", "description": "更新", "status": "busy",
        })
        detail = await client.get_agent(ds, sid)
        print(f"  endpoint preserved: {detail.metadata['endpoint']}")

        print("\n[non-dict card → ValueError]")
        for bad in [None, [1], "card", 42]:
            try:
                await client.replace_agent_card(ds, sid, bad)
            except ValueError as exc:
                print(f"  card={bad!r:>10}: {type(exc).__name__}")

        print("\n[foreign sid → NotOwnedError]")
        try:
            await client.replace_agent_card(ds, "never_owned", {"name": "x"})
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")

        print("\n[backend deleted → NotFoundError + cleanup]")
        # Use a sync sibling client to bypass ownership and delete the sid
        with A2XRegistryClient(base_url=base_url, ownership_file=False) as other:
            other._owned.add(ds, sid)
            other.deregister_agent(ds, sid)
        try:
            await client.replace_agent_card(ds, sid, {
                "name": "x", "description": "y", "endpoint": "http://teammate:8080",
            })
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  L1 cleaned: {(ds, sid) not in client._blank_endpoints}")

        await client.delete_dataset(ds)

    print("\n[network failure]")
    try:
        async with AsyncA2XRegistryClient(base_url="http://127.0.0.1:8999",
                                  ownership_file=False, timeout=2.0) as bad_client:
            bad_client._owned.add("ds", "sid")
            await bad_client.replace_agent_card("ds", "sid",
                                                {"name": "x", "description": "y",
                                                 "endpoint": "http://e"})
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
