import asyncio
import contextlib
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

import httpx


class A2XBackendClient:
    def __init__(
        self,
        repo_root: str | Path,
        host: str = "127.0.0.1",
        port: int = 8000,
        python_executable: str | None = None,
        startup_timeout: float = 30.0,
        request_timeout: float = 120.0,
        auto_start: bool = True,
    ):
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.python_executable = python_executable or shutil.which("python3.11") or sys.executable
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.auto_start = auto_start

        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._log_task: asyncio.Task[None] | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.request_timeout,
        )
        if self.auto_start:
            await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def start(self) -> None:
        if self._proc is not None:
            return

        if not (self.repo_root / "src").exists():
            raise RuntimeError(
                f"repo_root={self.repo_root} 看起来不是 A2X 仓库根目录（缺少 src/）"
            )

        if self._is_port_open():
            raise RuntimeError(
                f"{self.host}:{self.port} 已被占用，无法启动 A2X 后端"
            )

        self._proc = await asyncio.create_subprocess_exec(
            self.python_executable,
            "-m",
            "src.backend",
            "--host",
            self.host,
            "--port",
            str(self.port),
            cwd=str(self.repo_root),
            env=self._build_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        self._log_task = asyncio.create_task(self._drain_logs())
        await self._wait_for_http_ready()

    async def stop(self) -> None:
        if self._proc is None:
            return

        proc = self._proc

        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

        if self._log_task:
            try:
                await asyncio.wait_for(self._log_task, timeout=2)
            except asyncio.TimeoutError:
                self._log_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._log_task
            self._log_task = None

        self._proc = None

    async def aclose(self) -> None:
        await self.stop()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _drain_logs(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return

        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            print(f"[a2x-backend] {line.decode(errors='replace').rstrip()}")

    def _is_port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((self.host, self.port)) == 0

    def _build_subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Reduce noisy shutdown warnings and avoid extra tokenizer worker state.
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
        warnings = env.get("PYTHONWARNINGS", "")
        extra = "ignore:resource_tracker:UserWarning"
        if extra not in warnings:
            env["PYTHONWARNINGS"] = ",".join(filter(None, [warnings, extra]))
        return env

    async def _wait_for_http_ready(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout

        while True:
            if self._proc and self._proc.returncode is not None:
                raise RuntimeError(f"A2X 后端启动失败，退出码={self._proc.returncode}")

            try:
                await self.get("/api/warmup-status")
                return
            except Exception:
                pass

            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("等待 A2X 后端 HTTP 就绪超时")

            await asyncio.sleep(0.5)

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("AsyncClient 尚未初始化，请使用 `async with` 或先创建 client")
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
                raise TimeoutError(f"等待 A2X 后端预热超时: {data}")
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
            json={
                "name": name,
                "embedding_model": embedding_model,
            },
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


async def main():
    repo_root = Path(__file__).resolve().parent.parent
    async with A2XBackendClient(repo_root=repo_root) as client:
        print("Waiting for backend warmup...")
        warmup_status = await client.wait_until_warm()
        print("Warmup status:", warmup_status)

        dataset_name = "client_smoke_test_ds"
        datasets = await client.list_datasets()
        dataset_names = {item["name"] for item in datasets}
        if dataset_name not in dataset_names:
            created = await client.create_dataset(dataset_name)
            print("Created dataset:", created)
        else:
            print("Dataset already exists:", dataset_name)

        registered = await client.register_generic_service(
            dataset=dataset_name,
            name="天气查询服务",
            description="根据城市名称查询实时天气、温度、湿度以及未来天气预报。",
            url="https://api.example.com/weather",
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                },
                "required": ["city"],
            },
        )
        print("Registered service:", registered)

        try:
            search_results = await client.search(
                query="查询上海天气",
                method="vector",
                dataset=dataset_name,
                top_k=5,
            )
            print("Search results:", search_results)
        except httpx.HTTPStatusError as exc:
            print(
                "Demo search failed:",
                exc,
                "\nThis usually means the backend dependencies or dataset assets for search are not fully installed yet.",
            )

if __name__ == "__main__":
    asyncio.run(main())
