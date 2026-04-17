import argparse
import asyncio
from typing import Any

import httpx


class A2XRemoteClient:
    def __init__(
        self,
        base_url: str,
        request_timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.request_timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("AsyncClient is not initialized; use `async with`.")
        return self._client

    async def get(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._require_client().get(path, **kwargs)
        resp.raise_for_status()
        return resp

    async def post(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._require_client().post(path, **kwargs)
        resp.raise_for_status()
        return resp

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._require_client().delete(path, **kwargs)
        resp.raise_for_status()
        return resp

    async def get_warmup_status(self) -> dict[str, Any]:
        resp = await self.get("/api/warmup-status")
        return resp.json()

    async def wait_until_warm(
        self,
        poll_interval: float = 1.0,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        deadline = None
        if timeout is not None:
            deadline = asyncio.get_running_loop().time() + timeout

        while True:
            data = await self.get_warmup_status()
            if data.get("ready"):
                return data
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Timed out waiting for A2X backend warmup: {data}")
            await asyncio.sleep(poll_interval)

    async def list_datasets(self) -> list[dict[str, Any]]:
        resp = await self.get("/api/datasets")
        return resp.json()

    async def create_dataset(
        self,
        name: str,
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> dict[str, Any]:
        resp = await self.post(
            "/api/datasets",
            json={"name": name, "embedding_model": embedding_model},
        )
        return resp.json()

    async def register_generic_service(
        self,
        dataset: str,
        name: str,
        description: str,
        service_id: str | None = None,
        url: str = "",
        input_schema: dict[str, Any] | None = None,
        persistent: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "url": url,
            "inputSchema": input_schema or {},
            "persistent": persistent,
        }
        if service_id:
            payload["service_id"] = service_id

        resp = await self.post(
            "/api/datasets/{}/services/generic".format(dataset),
            json=payload,
        )
        return resp.json()

    async def search(
        self,
        query: str,
        method: str = "vector",
        dataset: str = "ToolRet_clean",
        top_k: int = 10,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        resp = await self.post(
            "/api/search",
            timeout=timeout,
            json={
                "query": query,
                "method": method,
                "dataset": dataset,
                "top_k": top_k,
            },
        )
        return resp.json()


async def run_smoke_test(args) -> None:
    async with A2XRemoteClient(args.base_url, request_timeout=args.timeout) as client:
        warmup = await client.get_warmup_status()
        print("Warmup:", warmup)

        datasets = await client.list_datasets()
        print("Datasets:", datasets)

        if args.check_only:
            return

        if not warmup.get("ready"):
            warmup = await client.wait_until_warm(timeout=args.wait_timeout)
            print("Warmup ready:", warmup)

        dataset_names = {item["name"] for item in datasets}
        if args.dataset not in dataset_names:
            created = await client.create_dataset(args.dataset)
            print("Created dataset:", created)
        else:
            print("Dataset already exists:", args.dataset)

        registered = await client.register_generic_service(
            dataset=args.dataset,
            name="天气查询服务",
            description="根据城市名称查询实时天气、温度、湿度以及未来天气预报。",
            url="https://api.example.com/weather",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        print("Registered service:", registered)

        result = await client.search(
            query="查询上海天气",
            method="vector",
            dataset=args.dataset,
            top_k=5,
        )
        print("Search result:", result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connect to a remote A2X backend and call its FastAPI APIs."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="A2X backend URL, for example: http://192.168.1.10:8000",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--wait-timeout", type=float, default=300.0)
    parser.add_argument("--dataset", default="client_smoke_test_ds")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check connectivity and lightweight APIs; do not wait for warmup or run search.",
    )
    args = parser.parse_args()

    asyncio.run(run_smoke_test(args))


if __name__ == "__main__":
    main()
