"""End-to-end smoke runner for the A2X Client SDK examples.

This script follows the "Agent Team 完整流程" in ``docs/client_design.md``
and exercises the public SDK methods in one coherent workflow.

By default it runs both:

1. a synchronous flow with ``A2XClient``
2. an asynchronous flow with ``AsyncA2XClient``

Run:
    python examples/client_sdk/run_all_examples.py
    python examples/client_sdk/run_all_examples.py --mode sync
    python examples/client_sdk/run_all_examples.py --mode async

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import A2XClient, AsyncA2XClient, ValidationError


def make_card(
    name: str,
    description: str,
    *,
    skills: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    card: dict[str, object] = {
        "protocolVersion": "0.0",
        "name": name,
        "description": description,
    }
    if skills:
        card["skills"] = skills
    return card


def ensure_absent_sync(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


async def ensure_absent_async(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
    except ValidationError:
        pass


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def run_sync_flow(base_url: str) -> None:
    dataset = "client_sdk_smoke_sync"
    ownership_file = Path(tempfile.gettempdir()) / "a2x_smoke_sync_owned.json"

    print("\n=== Sync Smoke ===")
    print(f"backend:        {base_url}")
    print(f"dataset:        {dataset}")
    print(f"ownership file: {ownership_file}")

    with A2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        ensure_absent_sync(client, dataset)

        print("[1/9] create_dataset")
        created = client.create_dataset(dataset)
        assert_equal(created.dataset, dataset, "create_dataset.dataset")

        print("[2/9] register_agent x2")
        planner = client.register_agent(
            dataset,
            make_card(
                "Task Planner",
                "拆解复杂任务为可执行子任务",
            ),
            service_id="smoke_sync_planner",
            persistent=True,
        )
        researcher = client.register_agent(
            dataset,
            make_card(
                "Web Researcher",
                "基于关键词检索网页并摘要",
                skills=[
                    {"name": "search", "description": "搜索互联网"},
                    {"name": "summarize", "description": "提炼关键信息"},
                ],
            ),
            service_id="smoke_sync_researcher",
            persistent=True,
        )
        assert_equal(planner.service_id, "smoke_sync_planner", "planner.service_id")
        assert_equal(researcher.service_id, "smoke_sync_researcher", "researcher.service_id")

        print("[3/9] set_team_count -> 0")
        client.set_team_count(dataset, planner.service_id, 0)
        client.set_team_count(dataset, researcher.service_id, 0)

        print("[4/9] update_agent")
        patch = client.update_agent(
            dataset=dataset,
            service_id=researcher.service_id,
            fields={"description": "检索 + 摘要 + 多语种翻译"},
        )
        assert_true("description" in patch.changed_fields, "researcher description should change")

        print("[5/9] set_team_count -> 2")
        planner_patch = client.set_team_count(dataset, planner.service_id, 2)
        researcher_patch = client.set_team_count(dataset, researcher.service_id, 2)
        assert_equal(planner_patch.changed_fields, ["agentTeamCount"], "planner changed_fields")
        assert_equal(researcher_patch.changed_fields, ["agentTeamCount"], "researcher changed_fields")
        assert_equal(planner_patch.taxonomy_affected, False, "planner taxonomy_affected")
        assert_equal(researcher_patch.taxonomy_affected, False, "researcher taxonomy_affected")

        print("[6/9] list_agents")
        briefs = client.list_agents(dataset)
        assert_equal(len(briefs), 2, "list_agents count before deregister")
        for brief in briefs:
            print(f"  - {brief.id}: {brief.name}")

        print("[7/9] get_agent")
        detail = client.get_agent(dataset, planner.service_id)
        assert_equal(detail.name, "Task Planner", "planner detail.name")
        assert_equal(detail.metadata.get("agentTeamCount"), 2, "planner team count")
        print(f"  planner team count: {detail.metadata.get('agentTeamCount')}")

        print("[8/9] deregister_agent")
        removed = client.deregister_agent(dataset, researcher.service_id)
        assert_equal(removed.status, "deregistered", "deregister status")
        remaining = client.list_agents(dataset)
        assert_equal(len(remaining), 1, "list_agents count after deregister")
        assert_equal(remaining[0].id, planner.service_id, "remaining service id")

        print("[9/9] delete_dataset")
        deleted = client.delete_dataset(dataset)
        assert_equal(deleted.status, "deleted", "delete_dataset status")

    print("sync smoke passed")


async def run_async_flow(base_url: str) -> None:
    dataset = "client_sdk_smoke_async"
    ownership_file = Path(tempfile.gettempdir()) / "a2x_smoke_async_owned.json"

    print("\n=== Async Smoke ===")
    print(f"backend:        {base_url}")
    print(f"dataset:        {dataset}")
    print(f"ownership file: {ownership_file}")

    async with AsyncA2XClient(base_url=base_url, ownership_file=ownership_file) as client:
        await ensure_absent_async(client, dataset)

        print("[1/8] create_dataset")
        created = await client.create_dataset(dataset)
        assert_equal(created.dataset, dataset, "create_dataset.dataset")

        print("[2/8] register_agent x2")
        planner = await client.register_agent(
            dataset,
            make_card(
                "Planner",
                "拆解复杂任务",
            ),
            service_id="smoke_async_planner",
            persistent=True,
        )
        researcher = await client.register_agent(
            dataset,
            make_card(
                "Researcher",
                "网页检索与摘要",
            ),
            service_id="smoke_async_researcher",
            persistent=True,
        )

        print("[3/8] set_team_count -> 0 (parallel)")
        await asyncio.gather(
            client.set_team_count(dataset, planner.service_id, 0),
            client.set_team_count(dataset, researcher.service_id, 0),
        )

        print("[4/8] get_agent x2 (parallel)")
        details = await asyncio.gather(
            client.get_agent(dataset, planner.service_id),
            client.get_agent(dataset, researcher.service_id),
        )
        for detail in details:
            assert_equal(detail.metadata.get("agentTeamCount"), 0, f"{detail.id} initial team count")
            print(f"  - {detail.name}: team count={detail.metadata.get('agentTeamCount')}")

        print("[5/8] update_agent + set_team_count -> 2")
        patch = await client.update_agent(
            dataset=dataset,
            service_id=researcher.service_id,
            fields={"description": "检索 + 摘要 + 多语种翻译"},
        )
        assert_true("description" in patch.changed_fields, "async researcher description should change")
        planner_patch, researcher_patch = await asyncio.gather(
            client.set_team_count(dataset, planner.service_id, 2),
            client.set_team_count(dataset, researcher.service_id, 2),
        )
        assert_equal(planner_patch.changed_fields, ["agentTeamCount"], "async planner changed_fields")
        assert_equal(researcher_patch.changed_fields, ["agentTeamCount"], "async researcher changed_fields")

        print("[6/8] list_agents")
        briefs = await client.list_agents(dataset)
        assert_equal(len(briefs), 2, "async list_agents count before deregister")
        for brief in briefs:
            print(f"  - {brief.id}: {brief.name}")

        print("[7/8] get_agent + deregister_agent")
        planner_detail = await client.get_agent(dataset, planner.service_id)
        assert_equal(planner_detail.metadata.get("agentTeamCount"), 2, "async planner team count")
        removed = await client.deregister_agent(dataset, researcher.service_id)
        assert_equal(removed.status, "deregistered", "async deregister status")
        remaining = await client.list_agents(dataset)
        assert_equal(len(remaining), 1, "async list_agents count after deregister")
        assert_equal(remaining[0].id, planner.service_id, "async remaining service id")

        print("[8/8] delete_dataset")
        deleted = await client.delete_dataset(dataset)
        assert_equal(deleted.status, "deleted", "async delete_dataset status")

    print("async smoke passed")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run A2X Client SDK smoke flows.")
    parser.add_argument(
        "--mode",
        choices=("sync", "async", "both"),
        default="both",
        help="Which flow to run. Default: both",
    )
    args = parser.parse_args()

    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")

    if args.mode in ("sync", "both"):
        run_sync_flow(base_url)

    if args.mode in ("async", "both"):
        await run_async_flow(base_url)

    print("\nAll requested smoke flows passed.")


if __name__ == "__main__":
    asyncio.run(main())
