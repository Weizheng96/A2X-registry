"""Asynchronous examples for ``AsyncA2XClient.deregister_agent``.

This file demonstrates:

1. successful deregistration after ownership is established
2. NotOwnedError (fail fast, no HTTP) for a service not owned by this client
3. NotFoundError after remote deletion, and local ownership auto-cleanup
4. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/deregister_agent_async.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
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
    A2XConnectionError,
    A2XHTTPError,
    AsyncA2XClient,
    NotFoundError,
    NotOwnedError,
    ValidationError,
)


def make_card(name: str, description: str) -> dict[str, str]:
    return {
        "protocolVersion": "0.0",
        "name": name,
        "description": description,
    }


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_deregister_async_owned.json"

    print(f"Using backend: {base_url}")
    print(f"Using ownership file: {ownership_file}")

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_deregister_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        # 1) Successful deregistration.
        print("\n[successful deregistration]")
        reg = await client.register_agent(
            ds,
            make_card("Deregister Async", "待注销服务"),
            service_id="agent_deregister_async",
            persistent=True,
        )
        result = await client.deregister_agent(ds, reg.service_id)
        print(f"  service_id: {result.service_id}")
        print(f"  status:     {result.status}")
        try:
            await client.get_agent(ds, reg.service_id)
        except NotFoundError as exc:
            print("  get_agent after deregister -> NotFoundError")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

        # 2) NotOwnedError: never owned by this client.
        print("\n[not owned -> fail fast]")
        try:
            await client.deregister_agent(ds, "never_owned_async")
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  message: {exc}")

        # 3) Two-step stale-ownership scenario:
        #    - first call: backend already deleted the service, so we expect 404
        #    - second call: the SDK has cleaned local ownership after that 404,
        #      so it now fails fast locally with NotOwnedError (no HTTP)
        print("\n[remote missing -> NotFoundError + ownership cleanup]")
        doomed = await client.register_agent(
            ds,
            make_card("Already Gone Async", "先删后再删"),
            service_id="agent_deregister_async_deleted",
            persistent=True,
        )

        async with AsyncA2XClient(base_url=base_url, ownership_file=False) as other:
            await asyncio.to_thread(other._owned.add, ds, doomed.service_id)
            await other.deregister_agent(ds, doomed.service_id)

        try:
            await client.deregister_agent(ds, doomed.service_id)
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

        try:
            await client.deregister_agent(ds, doomed.service_id)
        except NotOwnedError as exc:
            print(f"  caught after cleanup: {type(exc).__name__}")
            print(f"  message: {exc}")

        await client.delete_dataset(ds)

    # 4) Network / gateway failure.
    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999", ownership_file=False) as bad_client:
            await asyncio.to_thread(bad_client._owned.add, "bad_ds", "bad_sid")
            await bad_client.deregister_agent("bad_ds", "bad_sid")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  message: {exc}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  status_code: {exc.status_code}")
        print(f"  message: {exc}")
        print(f"  payload: {exc.payload}")


if __name__ == "__main__":
    asyncio.run(main())
