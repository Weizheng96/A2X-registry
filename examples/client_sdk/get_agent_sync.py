"""Synchronous examples for ``A2XClient.get_agent``.

This file demonstrates:

1. successful fetch of an A2A agent detail
2. ``metadata`` / ``raw`` fields on the returned ``AgentDetail``
3. NotFoundError when the service does not exist
4. UnexpectedServiceTypeError when the backend returns a skill ZIP
5. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/get_agent_sync.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path

import httpx

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.client import (
    A2XClient,
    A2XConnectionError,
    A2XHTTPError,
    NotFoundError,
    UnexpectedServiceTypeError,
    ValidationError,
)


def make_card(name: str, description: str) -> dict[str, str]:
    return {
        "protocolVersion": "0.0",
        "name": name,
        "description": description,
        "url": f"https://example.com/{name.lower().replace(' ', '-')}",
        "skills": [{"id": "search", "name": "Search", "description": "检索信息"}],
    }


def ensure_absent(client: A2XClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def build_skill_zip_bytes() -> bytes:
    buf = io.BytesIO()
    skill_md = """---
        name: Skill Sync Demo
        description: 一个用于测试 get_agent ZIP 分支的最小 skill
        license: MIT
        ---
        # Skill Sync Demo
        """
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", skill_md)
    return buf.getvalue()


def upload_skill(base_url: str, dataset: str) -> dict:
    files = {"file": ("skill_sync_demo.zip", build_skill_zip_bytes(), "application/zip")}
    with httpx.Client(base_url=base_url, timeout=30.0) as raw_client:
        resp = raw_client.post(f"api/datasets/{dataset}/skills", files=files)
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    print(f"Using backend: {base_url}")

    with A2XClient(base_url=base_url, ownership_file=False) as client:
        ds = "example_get_agent_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds, formats={"a2a": "v0.0", "skill": "v0.0"})

        reg = client.register_agent(
            ds,
            make_card("Planner Sync", "拆解任务并安排步骤"),
            service_id="agent_get_sync_planner",
            persistent=True,
        )

        print("\n[successful fetch]")
        detail = client.get_agent(ds, reg.service_id)
        print(f"  id:          {detail.id}")
        print(f"  type:        {detail.type}")
        print(f"  name:        {detail.name}")
        print(f"  description: {detail.description}")
        print(f"  metadata url: {detail.metadata.get('url')}")
        print(f"  raw keys:    {sorted(detail.raw.keys())}")

        print("\n[missing service -> NotFoundError]")
        try:
            client.get_agent(ds, "service_missing_sync")
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

        print("\n[skill ZIP -> UnexpectedServiceTypeError]")
        skill = upload_skill(base_url, ds)
        try:
            client.get_agent(ds, skill["service_id"])
        except UnexpectedServiceTypeError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  message: {exc}")

        client.delete_dataset(ds)

    print("\n[network / gateway failure]")
    try:
        with A2XClient(base_url="http://127.0.0.1:8999", ownership_file=False) as bad_client:
            bad_client.get_agent("example_get_agent_sync_unreachable", "bad_sid")
    except A2XConnectionError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  message: {exc}")
    except A2XHTTPError as exc:
        print(f"  caught: {type(exc).__name__}")
        print(f"  status_code: {exc.status_code}")
        print(f"  message: {exc}")
        print(f"  payload: {exc.payload}")


if __name__ == "__main__":
    main()
