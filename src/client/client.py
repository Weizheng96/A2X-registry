"""Synchronous client entry point.

``A2XClient`` composes an ``HTTPTransport`` + ``OwnershipStore`` and translates
each public method into one HTTP call plus (for mutating methods) an ownership
check / update. All business rules live in this module; network and
persistence concerns stay in their respective components.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from . import _internal as _i
from .errors import NotFoundError, NotOwnedError, ValidationError
from .models import (
    AgentBrief,
    AgentDetail,
    DatasetCreateResponse,
    DatasetDeleteResponse,
    DeregisterResponse,
    PatchResponse,
    RegisterResponse,
)
from .ownership import OwnershipStore
from .transport import HTTPTransport


class A2XClient:
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
        self._transport = HTTPTransport(
            base_url=self._base_url,
            timeout=timeout,
            headers=_i.build_default_headers(api_key),
        )
        self._owned = OwnershipStore(
            file_path=_i.resolve_ownership_file(ownership_file),
            base_url=self._base_url,
        )

    # ── Read-only config exposure ────────────────────────────────────────────
    # Stored as underscore attributes because changing them at runtime would
    # not reconnect the transport or re-scope the ownership file — documenting
    # the immutability via property is clearer than a writable attribute.

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

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "A2XClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ── Ownership guard ──────────────────────────────────────────────────────

    def _assert_owned(self, dataset: str, service_id: str) -> None:
        if not self._owned.contains(dataset, service_id):
            raise NotOwnedError(dataset, service_id)

    # ── Datasets ─────────────────────────────────────────────────────────────

    def create_dataset(
        self,
        name: str,
        embedding_model: str = "all-MiniLM-L6-v2",
        formats: Any = _i.UNSET,
    ) -> DatasetCreateResponse:
        body = _i.build_create_dataset_body(name, embedding_model, formats)
        resp = self._transport.request("POST", _i.DATASETS_ROOT, json=body)
        return DatasetCreateResponse.from_dict(resp.json())

    def delete_dataset(self, name: str) -> DatasetDeleteResponse:
        try:
            resp = self._transport.request("DELETE", _i.dataset_path(name))
        except ValidationError:
            # Backend 400 on dataset-missing is the only 400 case here;
            # clear local bookkeeping so subsequent calls stop failing. (D6)
            self._owned.remove_dataset(name)
            raise
        result = DatasetDeleteResponse.from_dict(resp.json())
        self._owned.remove_dataset(name)
        return result

    # ── Agents ───────────────────────────────────────────────────────────────

    def register_agent(
        self,
        dataset: str,
        agent_card: dict[str, Any],
        service_id: str | None = None,
        persistent: bool = True,
    ) -> RegisterResponse:
        body = _i.build_register_agent_body(agent_card, service_id, persistent)
        resp = self._transport.request("POST", _i.a2a_register_path(dataset), json=body)
        result = RegisterResponse.from_dict(resp.json())
        if persistent:
            # Backend discards non-persistent entries on restart, so persisting
            # ownership for them would cause later NotFoundError cascades. (D4)
            self._owned.add(dataset, result.service_id)
        return result

    def update_agent(
        self,
        dataset: str,
        service_id: str,
        fields: dict[str, Any],
    ) -> PatchResponse:
        self._assert_owned(dataset, service_id)
        try:
            resp = self._transport.request(
                "PUT", _i.service_path(dataset, service_id), json=fields
            )
        except NotFoundError:
            self._owned.remove(dataset, service_id)  # D3
            raise
        return PatchResponse.from_dict(resp.json())

    def set_team_count(
        self,
        dataset: str,
        service_id: str,
        count: int,
    ) -> PatchResponse:
        body = _i.build_team_count_body(count)
        self._assert_owned(dataset, service_id)
        try:
            resp = self._transport.request(
                "PUT", _i.service_path(dataset, service_id), json=body
            )
        except NotFoundError:
            self._owned.remove(dataset, service_id)  # D3
            raise
        return PatchResponse.from_dict(resp.json())

    def list_agents(self, dataset: str) -> list[AgentBrief]:
        resp = self._transport.request(
            "GET", _i.services_path(dataset), params={"mode": "browse"}
        )
        data = resp.json()
        return [AgentBrief.from_dict(d) for d in data]

    def get_agent(self, dataset: str, service_id: str) -> AgentDetail:
        resp = self._transport.request(
            "GET",
            _i.services_path(dataset),
            params={"mode": "single", "service_id": service_id},
        )
        return _i.parse_agent_detail(resp)

    def deregister_agent(self, dataset: str, service_id: str) -> DeregisterResponse:
        self._assert_owned(dataset, service_id)
        try:
            resp = self._transport.request("DELETE", _i.service_path(dataset, service_id))
        except NotFoundError:
            self._owned.remove(dataset, service_id)  # D3
            raise
        result = DeregisterResponse.from_dict(resp.json())
        self._owned.remove(dataset, service_id)
        return result
