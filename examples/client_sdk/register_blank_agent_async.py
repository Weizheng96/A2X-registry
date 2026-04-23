"""Asynchronous examples for ``AsyncA2XClient.register_blank_agent``.

Mirrors ``register_blank_agent_sync.py``.
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

from a2x_client import AsyncA2XClient, A2XConnectionError, A2XHTTPError, ValidationError


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_register_blank_async.json"

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_register_blank_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        print("\n[register blank agent]")
        resp = await client.register_blank_agent(ds, endpoint="http://teammate.example:8080")
        print(f"  service_id: {resp.service_id}, status: {resp.status}")

        detail = await client.get_agent(ds, resp.service_id)
        print(f"  description: {detail.metadata.get('description')!r}")
        print(f"  endpoint:    {detail.metadata.get('endpoint')!r}")

        print("\n[idempotent re-register]")
        resp2 = await client.register_blank_agent(ds, endpoint="http://teammate.example:8080")
        print(f"  status: {resp2.status}, same sid: {resp2.service_id == resp.service_id}")

        print("\n[bad endpoint → ValueError]")
        for bad in ["", None, 42]:
            try:
                await client.register_blank_agent(ds, endpoint=bad)
            except ValueError as exc:
                print(f"  endpoint={bad!r:>10}: {type(exc).__name__}")

        await client.delete_dataset(ds)

    print("\n[network failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999",
                                  ownership_file=False, timeout=2.0) as bad_client:
            await bad_client.register_blank_agent("example_unreachable", endpoint="http://x")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
