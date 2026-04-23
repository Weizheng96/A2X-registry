"""Async variant of ``reserve_blank_agents_sync.py``.

Same flow, exercised through ``AsyncA2XRegistryClient`` to verify the async path
produces identical semantics. See the sync example for narrative comments.

Run:
    python examples/client_sdk/reserve_blank_agents_async.py
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

from a2x_registry_client import AsyncA2XRegistryClient, ValidationError


async def ensure_absent(client: AsyncA2XRegistryClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ds = "example_reserve_async"

    teammate_owned = Path(tempfile.gettempdir()) / "a2x_example_reserve_teammate_async.json"
    leader_owned = Path(tempfile.gettempdir()) / "a2x_example_reserve_leader_async.json"
    print(f"Using backend: {base_url}")

    teammate = AsyncA2XRegistryClient(base_url=base_url, ownership_file=teammate_owned)
    leader_1 = AsyncA2XRegistryClient(base_url=base_url, ownership_file=leader_owned)
    leader_2 = AsyncA2XRegistryClient(base_url=base_url, ownership_file=False)

    try:
        await ensure_absent(teammate, ds)
        await teammate.create_dataset(ds)

        print("\n[1] teammate registers as blank")
        reg = await teammate.register_blank_agent(
            ds, endpoint="http://teammate-async", service_id="teammate_a",
        )
        sid = reg.service_id
        print(f"    blank registered: {sid}")

        print("\n[2] leader_1 reserves 1 blank for 30s")
        async with await leader_1.reserve_blank_agents(ds, n=1, ttl_seconds=30) as r1:
            print(f"    holder_id={r1.holder_id}")
            print(f"    reserved sids: {[a['id'] for a in r1.agents]}")

            print("\n[3] leader_2 tries to reserve concurrently — blocked")
            r2 = await leader_2.reserve_blank_agents(ds, n=1, ttl_seconds=30)
            print(f"    leader_2 got {len(r2.agents)} agents (expected 0)")
            await leader_2.release_reservation(r2)

            print("\n[4] P2P negotiate succeeds; teammate commits team card")
            await teammate.replace_agent_card(ds, sid, {
                "name": "Task Planner",
                "description": "active team member",
                "endpoint": "http://teammate-async",
                "status": "busy",
            })

        print("\n[5] async context exit — no-op (auto-hook already released)")

        print("\n[6] teammate restores to blank")
        await teammate.restore_to_blank(ds, sid)

        idle = await teammate.list_idle_blank_agents(ds, n=10)
        print(f"    idle agents now: {[a['id'] for a in idle]}")

        print("\n[7] negotiation failure path: async with frees lease on exit")
        try:
            async with await leader_1.reserve_blank_agents(ds, n=1, ttl_seconds=30) as r3:
                print(f"    reserved {[a['id'] for a in r3.agents]}")
                raise RuntimeError("simulated P2P negotiation failure")
        except RuntimeError as exc:
            print(f"    caught {type(exc).__name__}: {exc}")
        idle_after = await teammate.list_idle_blank_agents(ds, n=10)
        print(f"    idle agents after failure release: "
              f"{[a['id'] for a in idle_after]}")

        await teammate.delete_dataset(ds)

    finally:
        await teammate.aclose()
        await leader_1.aclose()
        await leader_2.aclose()


if __name__ == "__main__":
    asyncio.run(main())
