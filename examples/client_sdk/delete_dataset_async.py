"""Asynchronous examples for ``AsyncA2XClient.delete_dataset``.

This file demonstrates:

1. successful delete
2. validation error (dataset does not exist)
3. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/delete_dataset_async.py

Optional environment variables:
    A2X_BASE_URL   default: http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from a2x_client import (
    A2XConnectionError,
    A2XHTTPError,
    AsyncA2XClient,
    ValidationError,
)


async def ensure_absent(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
        print(f"  removed leftover dataset: {dataset}")
    except ValidationError:
        pass


def print_result(title: str, result) -> None:
    print(f"\n[{title}]")
    print(f"  dataset: {result.dataset}")
    print(f"  status:  {result.status}")


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")

    print(f"Using backend: {base_url}")

    async with AsyncA2XClient(base_url=base_url) as client:
        ds_success = "example_delete_async_success"
        ds_missing = "example_delete_async_missing"

        await ensure_absent(client, ds_success)
        await ensure_absent(client, ds_missing)

        # 1) Successful delete: create first, then delete.
        await client.create_dataset(ds_success)
        result = await client.delete_dataset(ds_success)
        print_result("successful delete", result)

        # 2) ValidationError: dataset does not exist.
        print("\n[validation error: dataset missing]")
        try:
            await client.delete_dataset(ds_missing)
        except ValidationError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

    # 3) Network / gateway failure.
    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999") as bad_client:
            await bad_client.delete_dataset("example_delete_async_unreachable")
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
