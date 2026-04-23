"""Synchronous examples for ``A2XRegistryClient.update_agent``.

This file demonstrates:

1. successful update after ownership is established via register_agent()
2. ``changed_fields`` and ``taxonomy_affected`` in PatchResponse
3. NotOwnedError (fail fast, no HTTP) for a service not owned by this client
4. NotFoundError after remote deletion, then a second call fails with NotOwnedError
5. ValidationError for a type-invalid update payload
6. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/update_agent_sync.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import httpx

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from a2x_registry_client import (
    A2XRegistryClient,
    A2XConnectionError,
    A2XHTTPError,
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


def ensure_absent(client: A2XRegistryClient, dataset: str) -> None:
    try:
        client.delete_dataset(dataset)
    except ValidationError:
        pass


def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")
    ownership_file = Path(tempfile.gettempdir()) / "a2x_example_update_sync_owned.json"

    print(f"Using backend: {base_url}")
    print(f"Using ownership file: {ownership_file}")

    with A2XRegistryClient(base_url=base_url, ownership_file=ownership_file) as client:
        ds = "example_update_sync"
        ensure_absent(client, ds)
        client.create_dataset(ds)

        reg = client.register_agent(
            ds,
            make_card("Updater Sync", "原始描述"),
            service_id="agent_update_sync",
            persistent=True,
        )
        print("\n[setup]")
        print(f"  registered service_id: {reg.service_id}")

        # 1) Successful update with business-relevant field.
        print("\n[successful update]")
        patch = client.update_agent(
            ds,
            reg.service_id,
            {"description": "更新后的描述"},
        )
        print(f"  service_id:         {patch.service_id}")
        print(f"  dataset:            {patch.dataset}")
        print(f"  status:             {patch.status}")
        print(f"  changed_fields:     {patch.changed_fields}")
        print(f"  taxonomy_affected:  {patch.taxonomy_affected}")

        # 2) Update a non-taxonomy field.
        print("\n[non-taxonomy update]")
        patch2 = client.update_agent(
            ds,
            reg.service_id,
            {"url": "https://example.com/updated"},
        )
        print(f"  changed_fields:     {patch2.changed_fields}")
        print(f"  taxonomy_affected:  {patch2.taxonomy_affected}")

        # 3) NotOwnedError: another service id that was never owned.
        print("\n[not owned -> fail fast]")
        try:
            client.update_agent(
                ds,
                "never_owned_sync",
                {"description": "should not reach server"},
            )
        except NotOwnedError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  message: {exc}")

        # 4) NotFoundError: delete remotely first, then update. The SDK should
        # auto-remove local ownership, so a second call fails fast with
        # NotOwnedError instead of hitting the server again.
        print("\n[remote missing -> NotFoundError + ownership cleanup]")
        doomed = client.register_agent(
            ds,
            make_card("To Be Deleted Sync", "soon gone"),
            service_id="agent_update_sync_deleted",
            persistent=True,
        )
        # Delete the service out-of-band so the local ownership record stays
        # stale; the next update_agent() should hit backend 404, then the SDK
        # auto-cleans local ownership and re-raises NotFoundError.
        delete_url = f"{client.base_url}api/datasets/{ds}/services/{doomed.service_id}"
        resp = httpx.delete(delete_url, timeout=30.0)
        resp.raise_for_status()
        try:
            client.update_agent(
                ds,
                doomed.service_id,
                {"description": "after delete"},
            )
        except NotFoundError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")
        try:
            client.update_agent(
                ds,
                doomed.service_id,
                {"description": "second try should fail locally"},
            )
        except NotOwnedError as exc:
            print(f"  caught after cleanup: {type(exc).__name__}")
            print(f"  message: {exc}")

        # 5) ValidationError: known field with invalid type for AgentCard.
        print("\n[validation error: invalid field type]")
        try:
            client.update_agent(
                ds,
                reg.service_id,
                {"skills": "not-a-list"},
            )
        except ValidationError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

        client.delete_dataset(ds)

    # 6) Network / gateway failure.
    print("\n[network / gateway failure]")
    try:
        with A2XRegistryClient(base_url="http://127.0.0.1:8999", ownership_file=False) as bad_client:
            # Build ownership first so the call reaches the transport layer.
            bad_client._owned.add("bad_ds", "bad_sid")
            bad_client.update_agent("bad_ds", "bad_sid", {"description": "x"})
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
