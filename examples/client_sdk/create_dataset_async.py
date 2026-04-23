"""Asynchronous examples for ``AsyncA2XClient.create_dataset``.

This file mirrors the sync example and covers:

1. default formats
2. explicit ``formats=None``
3. custom embedding model
4. custom formats
5. validation error (invalid / empty formats)
6. validation error (duplicate dataset)
7. network / gateway failure (backend unreachable)

Run:
    python examples/client_sdk/create_dataset_async.py

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


async def cleanup_dataset(client: AsyncA2XClient, dataset: str) -> None:
    try:
        await client.delete_dataset(dataset)
        print(f"  cleaned old dataset: {dataset}")
    except ValidationError:
        pass


def print_result(title: str, result) -> None:
    print(f"\n[{title}]")
    print(f"  dataset:         {result.dataset}")
    print(f"  embedding_model: {result.embedding_model}")
    print(f"  formats:         {result.formats}")
    print(f"  status:          {result.status}")


async def main() -> None:
    base_url = os.getenv("A2X_BASE_URL", "http://127.0.0.1:8000")

    print(f"Using backend: {base_url}")

    async with AsyncA2XClient(base_url=base_url) as client:
        ds_default = "example_async_default"
        ds_none = "example_async_none"
        ds_embedding = "example_async_embedding"
        ds_formats = "example_async_formats"
        ds_duplicate = "example_async_duplicate"

        for ds in (ds_default, ds_none, ds_embedding, ds_formats, ds_duplicate):
            await cleanup_dataset(client, ds)

        # 1) Default behavior: SDK injects {"a2a": "v0.0"}.
        result = await client.create_dataset(ds_default)
        print_result("default formats", result)

        # 2) Explicit None: omit "formats" from request body.
        result = await client.create_dataset(ds_none, formats=None)
        print_result("explicit formats=None", result)

        # 3) Custom embedding model.
        result = await client.create_dataset(
            ds_embedding,
            embedding_model="paraphrase-multilingual-MiniLM-L12-v2",
        )
        print_result("custom embedding model", result)

        # 4) Custom formats.
        result = await client.create_dataset(
            ds_formats,
            formats={"a2a": "v0.0", "generic": "v0.0"},
        )
        print_result("custom formats", result)

        # 5) ValidationError: invalid / empty formats.
        print("\n[validation error: empty formats]")
        try:
            await client.create_dataset("example_async_bad_formats", formats={})
        except ValidationError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

        # 6) ValidationError: duplicate dataset.
        print("\n[validation error: duplicate dataset]")
        first = await client.create_dataset(ds_duplicate)
        print(f"  first create ok: {first.dataset}")
        try:
            await client.create_dataset(ds_duplicate)
        except ValidationError as exc:
            print(f"  caught: {type(exc).__name__}")
            print(f"  status_code: {exc.status_code}")
            print(f"  payload: {exc.payload}")

    # 7) Network / gateway failure: the exact exception depends on the runtime
    # environment. Without a proxy you usually get A2XConnectionError; behind
    # a proxy you may see an HTTP 502/504 wrapped as A2XHTTPError.
    print("\n[network / gateway failure]")
    try:
        async with AsyncA2XClient(base_url="http://127.0.0.1:8999") as bad_client:
            await bad_client.create_dataset("example_async_unreachable")
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
