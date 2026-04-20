"""Asynchronous examples for ``AsyncA2XClient.set_team_count``.

This file demonstrates:

1. successful team-count updates after ownership is established
2. ``changed_fields`` and ``taxonomy_affected`` in PatchResponse
3. reading back ``agentTeamCount`` via ``get_agent()``
4. ValueError for invalid count (local validation, no HTTP)
5. NotOwnedError (fail fast, no HTTP) for a service not owned by this client
6. NotFoundError after remote deletion, then a second call fails with NotOwnedError
7. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/set_team_count_async.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx

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
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_team_count_async_owned.json"

    print(f"Using backend: {base_url}")
    print(f"Using ownership file: {ownership_file}")

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_team_count_async"
        await ensure_absent(client, ds)
        await client.create_dataset(ds)

        reg = await client.register_agent(
            ds,
            make_card("Planner Async", "负责拆解与编排"),
            service_id="agent_team_count_async",
            persistent=True,
        )
        print("\n[setup]")
        print(f"  registered service_id: {reg.service_id}")

        print("\n[successful update -> count = 0]")
        patch0 = await client.set_team_count(ds, reg.service_id, 0)
        print(f"  status:             {patch0.status}")
        print(f"  changed_fields:     {patch0.changed_fields}")
        print(f"  taxonomy_affected:  {patch0.taxonomy_affected}")

        print("\n[successful update -> count = 2]")
        patch2 = await client.set_team_count(ds, reg.service_id, 2)
        print(f"  status:             {patch2.status}")
        print(f"  changed_fields:     {patch2.changed_fields}")
        print(f"  taxonomy_affected:  {patch2.taxonomy_affected}")

        detail = await client.get_agent(ds, reg.service_id)
        print("  read back via get_agent")
        print(f"  metadata.agentTeamCount: {detail.metadata.get('agentTeamCount')}")

        print("\n[invalid count -> ValueError before HTTP]")
        try:
            await client.set_team_count(ds, reg.service_id, -1)
        except ValueError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  message: {exc}")

        print("\n[not owned -> fail fast]")
        try:
            await client.set_team_count(ds, "never_owned_async", 1)
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  message: {exc}")

        print("\n[remote missing -> NotFoundError + ownership cleanup]")
        doomed = await client.register_agent(
            ds,
            make_card("To Be Deleted Async", "马上被删掉"),
            service_id="agent_team_count_async_deleted",
            persistent=True,
        )
        delete_url = f"{client.base_url}api/datasets/{ds}/services/{doomed.service_id}"
        async with httpx.AsyncClient(timeout=30.0) as raw_client:
            delete_resp = await raw_client.delete(delete_url)
            delete_resp.raise_for_status()
        try:
            await client.set_team_count(ds, doomed.service_id, 3)
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")
        try:
            await client.set_team_count(ds, doomed.service_id, 4)
        except NotOwnedError as exc:
            print(f"  caught after cleanup: {type(exc).__name__}")
            print(f"  message: {exc}")

        await client.delete_dataset(ds)

    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999", ownership_file=False) as bad_client:
            bad_client._owned.add("bad_ds", "bad_sid")
            await bad_client.set_team_count("bad_ds", "bad_sid", 1)
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
