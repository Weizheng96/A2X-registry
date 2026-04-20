"""Asynchronous examples for ``AsyncA2XClient.register_agent``.

This file demonstrates:

1. persistent=True registers ownership, so later update_agent() is allowed
2. re-registering the same explicit service_id returns ``status="updated"``
3. persistent=False skips ownership, so later update_agent() raises NotOwnedError
4. validation error (invalid agent card)
5. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/register_agent_async.py

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
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_register_async_owned.json"

    print(f"Using backend: {base_url}")
    print(f"Using ownership file: {ownership_file}")

    async with AsyncA2XClient(
        base_url=base_url,
        ownership_file=ownership_file,
    ) as client:
        ds_persistent = "example_register_async_persistent"
        ds_ephemeral = "example_register_async_ephemeral"

        await ensure_absent(client, ds_persistent)
        await ensure_absent(client, ds_ephemeral)

        await client.create_dataset(ds_persistent)
        await client.create_dataset(ds_ephemeral)

        # 1) persistent=True: ownership is recorded, so later update_agent works.
        print("\n[persistent=True -> ownership recorded]")
        reg = await client.register_agent(
            ds_persistent,
            make_card("Planner Async", "拆解复杂任务"),
            service_id="agent_async_planner",
            persistent=True,
        )
        print(f"  service_id: {reg.service_id}")
        print(f"  dataset:    {reg.dataset}")
        print(f"  status:     {reg.status}")

        patch = await client.update_agent(
            ds_persistent,
            reg.service_id,
            {"description": "拆解复杂任务并分发执行"},
        )
        print("  update_agent after register succeeded")
        print(f"  changed_fields:    {patch.changed_fields}")
        print(f"  taxonomy_affected: {patch.taxonomy_affected}")

        # 2) same explicit service_id -> backend returns updated.
        print("\n[explicit service_id re-register -> updated]")
        reg2 = await client.register_agent(
            ds_persistent,
            make_card("Planner Async", "新描述，覆盖已有条目"),
            service_id="agent_async_planner",
            persistent=True,
        )
        print(f"  service_id: {reg2.service_id}")
        print(f"  status:     {reg2.status}")

        # 3) persistent=False: ownership not recorded, later mutation fails fast.
        print("\n[persistent=False -> NotOwnedError on later mutation]")
        ephemeral = await client.register_agent(
            ds_ephemeral,
            make_card("Ephemeral Async", "临时注册，不落 ownership"),
            service_id="agent_async_ephemeral",
            persistent=False,
        )
        print(f"  service_id: {ephemeral.service_id}")
        print(f"  status:     {ephemeral.status}")
        try:
            await client.update_agent(
                ds_ephemeral,
                ephemeral.service_id,
                {"description": "这一步应该在本地 ownership 检查时失败"},
            )
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  message: {exc}")

        # 4) ValidationError: invalid card.
        print("\n[validation error: invalid agent card]")
        try:
            await client.register_agent(
                ds_persistent,
                {"protocolVersion": "0.0", "name": "Missing Description"},
                service_id="agent_async_invalid",
            )
        except ValidationError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

        # Clean up datasets created by this example.
        await client.delete_dataset(ds_persistent)
        await client.delete_dataset(ds_ephemeral)

    # 5) Network / gateway failure.
    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(
            base_url="http://127.0.0.1:8999",
            ownership_file=False,
        ) as bad_client:
            await bad_client.register_agent(
                "example_register_async_unreachable",
                make_card("Bad Async", "backend unreachable"),
            )
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
