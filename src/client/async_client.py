"""Asynchronous client entry point.

``AsyncA2XClient`` mirrors ``A2XClient`` one-to-one: same methods, same
parameters, same return types, same exceptions — only every method is a
coroutine and HTTP flows through ``httpx.AsyncClient``. ``OwnershipStore`` is
synchronous by design, so its writes are dispatched via
``asyncio.to_thread`` to keep the event loop unblocked.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from . import _internal as _i
from .errors import NotFoundError, NotOwnedError, ValidationError
from .models import (
    AgentDetail,
    DatasetCreateResponse,
    DatasetDeleteResponse,
    DeregisterResponse,
    PatchResponse,
    RegisterResponse,
)
from .ownership import OwnershipStore
from .transport import AsyncHTTPTransport


class AsyncA2XClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        api_key: str | None = None,
        ownership_file: Path | str | Literal[False] | None = None,
    ) -> None:
        self._base_url = _i.normalize_base_url(base_url)
        self._timeout = timeout
        self._api_key = api_key
        self._transport = AsyncHTTPTransport(
            base_url=self._base_url,
            timeout=timeout,
            headers=_i.build_default_headers(api_key),
        )
        self._owned = OwnershipStore(
            file_path=_i.resolve_ownership_file(ownership_file),
            base_url=self._base_url,
        )
        # L1 cache for restore_to_blank (see A2XClient.__init__ for rationale).
        # Pure in-memory dict; the event loop serialises access so no lock needed.
        self._blank_endpoints: dict[tuple[str, str], str] = {}

    # ── Read-only config exposure ────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def api_key(self) -> str | None:
        return self._api_key

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> "AsyncA2XClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    # ── Ownership guard ──────────────────────────────────────────────────────

    def _assert_owned(self, dataset: str, service_id: str) -> None:
        # Pure in-memory check — no need to leave the event loop.
        if not self._owned.contains(dataset, service_id):
            raise NotOwnedError(dataset, service_id)

    # ── Datasets ─────────────────────────────────────────────────────────────

    async def create_dataset(
        self,
        name: str,
        embedding_model: str = "all-MiniLM-L6-v2",
        formats: Any = _i.UNSET,
    ) -> DatasetCreateResponse:
        body = _i.build_create_dataset_body(name, embedding_model, formats)
        resp = await self._transport.request("POST", _i.DATASETS_ROOT, json=body)
        return DatasetCreateResponse.from_dict(resp.json())

    async def delete_dataset(self, name: str) -> DatasetDeleteResponse:
        try:
            resp = await self._transport.request("DELETE", _i.dataset_path(name))
        except ValidationError:
            await asyncio.to_thread(self._owned.remove_dataset, name)  # D6
            raise
        result = DatasetDeleteResponse.from_dict(resp.json())
        await asyncio.to_thread(self._owned.remove_dataset, name)
        return result

    # ── Agents ───────────────────────────────────────────────────────────────

    async def register_agent(
        self,
        dataset: str,
        agent_card: dict[str, Any],
        service_id: str | None = None,
        persistent: bool = True,
    ) -> RegisterResponse:
        body = _i.build_register_agent_body(agent_card, service_id, persistent)
        resp = await self._transport.request("POST", _i.a2a_register_path(dataset), json=body)
        result = RegisterResponse.from_dict(resp.json())
        if persistent:  # D4
            await asyncio.to_thread(self._owned.add, dataset, result.service_id)
        return result

    async def update_agent(
        self,
        dataset: str,
        service_id: str,
        fields: dict[str, Any],
    ) -> PatchResponse:
        self._assert_owned(dataset, service_id)
        try:
            resp = await self._transport.request(
                "PUT", _i.service_path(dataset, service_id), json=fields
            )
        except NotFoundError:
            await asyncio.to_thread(self._owned.remove, dataset, service_id)  # D3
            raise
        return PatchResponse.from_dict(resp.json())

    async def set_status(
        self,
        dataset: str,
        service_id: str,
        status: str,
    ) -> PatchResponse:
        """See ``A2XClient.set_status``."""
        body = _i.build_status_body(status)
        self._assert_owned(dataset, service_id)
        try:
            resp = await self._transport.request(
                "PUT", _i.service_path(dataset, service_id), json=body
            )
        except NotFoundError:
            await asyncio.to_thread(self._owned.remove, dataset, service_id)  # D3
            raise
        return PatchResponse.from_dict(resp.json())

    async def list_agents(
        self,
        dataset: str,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """See ``A2XClient.list_agents``."""
        params = _i.build_filter_params(filters)
        resp = await self._transport.request(
            "GET", _i.services_path(dataset), params=params
        )
        return _i.parse_agent_list(resp)

    async def get_agent(self, dataset: str, service_id: str) -> AgentDetail:
        """See ``A2XClient.get_agent``."""
        resp = await self._transport.request(
            "GET", _i.service_path(dataset, service_id)
        )
        return _i.parse_agent_detail(resp)

    async def deregister_agent(self, dataset: str, service_id: str) -> DeregisterResponse:
        self._assert_owned(dataset, service_id)
        try:
            resp = await self._transport.request(
                "DELETE", _i.service_path(dataset, service_id)
            )
        except NotFoundError:
            await asyncio.to_thread(self._owned.remove, dataset, service_id)  # D3
            self._blank_endpoints.pop((dataset, service_id), None)
            raise
        result = DeregisterResponse.from_dict(resp.json())
        await asyncio.to_thread(self._owned.remove, dataset, service_id)
        self._blank_endpoints.pop((dataset, service_id), None)
        return result

    # ── Team-agent helpers ───────────────────────────────────────────────────

    async def register_blank_agent(
        self,
        dataset: str,
        endpoint: str,
        service_id: str | None = None,
        persistent: bool = True,
    ) -> RegisterResponse:
        """See ``A2XClient.register_blank_agent``."""
        card = _i.build_blank_agent_card(endpoint)
        result = await self.register_agent(
            dataset, card, service_id=service_id, persistent=persistent
        )
        self._blank_endpoints[(dataset, result.service_id)] = endpoint
        return result

    async def list_idle_blank_agents(
        self,
        dataset: str,
        n: int = 1,
    ) -> list[dict[str, Any]]:
        """See ``A2XClient.list_idle_blank_agents``. One HTTP call."""
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"n must be a non-negative int, got {n!r}")
        if n == 0:
            return []

        agents = await self.list_agents(
            dataset,
            description=_i.BLANK_DESCRIPTION_SENTINEL,
            **{_i.STATUS_FIELD: _i.STATUS_ONLINE},
        )
        return agents[:n]

    async def replace_agent_card(
        self,
        dataset: str,
        service_id: str,
        agent_card: dict[str, Any],
    ) -> RegisterResponse:
        """See ``A2XClient.replace_agent_card``."""
        self._assert_owned(dataset, service_id)
        if not isinstance(agent_card, dict):
            raise ValueError(
                f"agent_card must be a dict, got {type(agent_card).__name__}: "
                f"{agent_card!r}"
            )

        endpoint = _i.extract_endpoint(agent_card)
        if endpoint is None:
            endpoint = await self._resolve_endpoint(dataset, service_id)
            agent_card = {**agent_card, _i.ENDPOINT_FIELD: endpoint}

        body = _i.build_register_agent_body(agent_card, service_id, persistent=True)
        try:
            resp = await self._transport.request(
                "POST", _i.a2a_register_path(dataset), json=body
            )
        except NotFoundError:
            await asyncio.to_thread(self._owned.remove, dataset, service_id)
            self._blank_endpoints.pop((dataset, service_id), None)
            raise
        result = RegisterResponse.from_dict(resp.json())
        await asyncio.to_thread(self._owned.add, dataset, result.service_id)
        self._blank_endpoints[(dataset, result.service_id)] = endpoint
        return result

    async def restore_to_blank(
        self,
        dataset: str,
        service_id: str,
    ) -> RegisterResponse:
        """See ``A2XClient.restore_to_blank``."""
        self._assert_owned(dataset, service_id)
        endpoint = await self._resolve_endpoint(dataset, service_id)
        card = _i.build_blank_agent_card(endpoint)
        # replace_agent_card refreshes the L1 cache on success
        return await self.replace_agent_card(dataset, service_id, card)

    async def _resolve_endpoint(self, dataset: str, service_id: str) -> str:
        """See ``A2XClient._resolve_endpoint``."""
        cached = self._blank_endpoints.get((dataset, service_id))
        if cached:
            return cached
        detail = await self.get_agent(dataset, service_id)
        endpoint = _i.extract_endpoint(detail.metadata)
        if endpoint is None:
            raise ValueError(
                f"No 'endpoint' available for service {service_id!r} in dataset "
                f"{dataset!r}: not in local L1 cache and not in current Agent Card. "
                "Provide 'endpoint' explicitly, or call register_blank_agent "
                "first to seed the cache."
            )
        return endpoint
