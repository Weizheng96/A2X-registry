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
        # L1 cache for restore_to_blank: {(dataset, service_id): endpoint}.
        # Populated by register_blank_agent / restore_to_blank in the same
        # process; not persisted (by design). L2 fallback in
        # restore_to_blank reads the endpoint from the current card.
        self._blank_endpoints: dict[tuple[str, str], str] = {}

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
            self._blank_endpoints.pop((dataset, service_id), None)
            raise
        result = DeregisterResponse.from_dict(resp.json())
        self._owned.remove(dataset, service_id)
        self._blank_endpoints.pop((dataset, service_id), None)
        return result

    # ── Team-agent helpers ───────────────────────────────────────────────────

    def register_blank_agent(
        self,
        dataset: str,
        endpoint: str,
        service_id: str | None = None,
        persistent: bool = True,
    ) -> RegisterResponse:
        """Register a blank/idle agent into the idle pool.

        The blank AgentCard is::

            {"name": "_BlankAgent_<endpoint>", "description": "_",
             "endpoint": endpoint, "agentTeamCount": 0}

        The ``name`` prefix is the discovery contract — ``list_idle_blank_agents``
        identifies blank entries by this prefix. Re-registering the same
        endpoint is idempotent (same sid → backend ``status="updated"``).
        """
        card = _i.build_blank_agent_card(endpoint)
        result = self.register_agent(
            dataset, card, service_id=service_id, persistent=persistent
        )
        self._blank_endpoints[(dataset, result.service_id)] = endpoint
        return result

    def list_agents_full(
        self,
        dataset: str,
        page: int = 1,
        size: int = -1,
    ) -> list[AgentDetail]:
        """Return full-metadata entries via ``GET ...?mode=full``.

        Backend quirk (see ``_internal.parse_full_list_servers``): a2a entries
        arrive as raw Agent Cards with no ``id`` wrapper, so for those
        ``AgentDetail.id`` is empty and ``AgentDetail.raw`` is the card.
        Generic/skill entries keep the ``{id, type, name, description,
        metadata}`` shape. Callers needing sids for a2a must cross-reference
        ``list_agents(dataset)``.
        """
        params = _i.build_full_list_params(page, size)
        resp = self._transport.request("GET", _i.services_path(dataset), params=params)
        servers = _i.parse_full_list_servers(resp)
        return [AgentDetail.from_dict(s) for s in servers]

    def list_agents_by_filter(
        self,
        dataset: str,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Bulk query via ``GET ...?mode=filter&<k>=<v>&...``.

        Every keyword argument becomes a query-param filter with AND semantics.
        Values are coerced to strings (HTTP query params are strings; backend
        also string-coerces both sides of the comparison). A service matches
        iff, for every filter ``(k, v)``, its type-specific raw dict contains
        ``k`` and ``str(raw[k]) == str(v)``.

        **Filter-target nuance** (mirrors backend ``_entry_filter_dict``):
        filters operate on the *raw* per-type data — ``entry.agent_card`` for
        a2a (so the original, non-transformed ``description``),
        ``entry.service_data`` for generic, ``entry.skill_data`` for skill.
        This differs from the ``build_description``-transformed
        ``description`` shown by browse/full.

        Returns the standard wrapped shape per match:
        ``[{id, type, name, description, metadata}]``. Call
        ``list_idle_blank_agents`` for the common blank-pool use case.
        """
        params = _i.build_filter_params(filters)
        resp = self._transport.request("GET", _i.services_path(dataset), params=params)
        data = resp.json()
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    def list_idle_blank_agents(
        self,
        dataset: str,
        n: int,
    ) -> list[dict[str, Any]]:
        """Return up to ``n`` blank agents' Agent Cards, ascending by ``agentTeamCount``.

        Single HTTP call: ``mode=filter&description=__BLANK__`` on the backend
        returns only entries whose raw card description matches the sentinel.
        SDK then sorts by ``agentTeamCount`` (missing → 0, most-idle) and
        extracts the Agent Card (``entry["metadata"]``) from each wrapped
        response.

        The returned dicts are the raw Agent Cards — read ``card["endpoint"]``
        and ``card.get("agentTeamCount", 0)`` directly.
        """
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"n must be a non-negative int, got {n!r}")
        if n == 0:
            return []

        wrapped = self.list_agents_by_filter(
            dataset, description=_i.BLANK_DESCRIPTION_SENTINEL
        )
        cards = [
            w["metadata"] for w in wrapped
            if isinstance(w.get("metadata"), dict)
        ]
        cards.sort(key=_i.extract_team_count)
        return cards[:n]

    def replace_agent_card(
        self,
        dataset: str,
        service_id: str,
        agent_card: dict[str, Any],
    ) -> RegisterResponse:
        """Fully replace an owned a2a agent's card (not a partial merge).

        Routes through ``POST /api/datasets/{ds}/services/a2a`` with the
        existing ``service_id``; ``_do_register`` replaces the whole entry
        (see ``src/register/service.py``), so omitted fields are dropped —
        the opposite of ``update_agent`` (PUT upsert).

        Enforces that ``agent_card`` contains a non-empty ``endpoint`` field
        (raises ``ValueError`` locally, no HTTP): ``restore_to_blank`` relies
        on this field for its L2 fallback across process restarts.
        """
        _i.assert_card_has_endpoint(agent_card)
        self._assert_owned(dataset, service_id)
        body = _i.build_register_agent_body(agent_card, service_id, persistent=True)
        try:
            resp = self._transport.request(
                "POST", _i.a2a_register_path(dataset), json=body
            )
        except NotFoundError:
            self._owned.remove(dataset, service_id)  # D3 parity
            self._blank_endpoints.pop((dataset, service_id), None)
            raise
        result = RegisterResponse.from_dict(resp.json())
        self._owned.add(dataset, result.service_id)  # idempotent
        return result

    def restore_to_blank(
        self,
        dataset: str,
        service_id: str,
    ) -> RegisterResponse:
        """Overwrite an owned agent with the blank card template.

        Endpoint resolution:
          - **L1**: in-memory cache populated at ``register_blank_agent`` /
            previous ``restore_to_blank`` — zero extra HTTP in the common
            single-process flow.
          - **L2**: ``get_agent`` reads ``endpoint`` from the current card
            (works across process restarts if the non-blank card preserved
            the field).
          - **L3**: ``ValueError`` if neither path yields an endpoint.
        """
        self._assert_owned(dataset, service_id)
        endpoint = self._resolve_blank_endpoint(dataset, service_id)
        card = _i.build_blank_agent_card(endpoint)
        result = self.replace_agent_card(dataset, service_id, card)
        self._blank_endpoints[(dataset, service_id)] = endpoint
        return result

    def _resolve_blank_endpoint(self, dataset: str, service_id: str) -> str:
        cached = self._blank_endpoints.get((dataset, service_id))
        if cached:
            return cached
        detail = self.get_agent(dataset, service_id)
        # mode=single wraps the card in ``metadata``; try both slots.
        endpoint = _i.extract_endpoint(detail.metadata) or _i.extract_endpoint(detail.raw)
        if endpoint is None:
            raise ValueError(
                f"Cannot restore {service_id!r} to blank: 'endpoint' missing "
                "from the current Agent Card. Either preserve the 'endpoint' "
                "field when calling replace_agent_card, or call "
                "register_blank_agent again with the desired endpoint."
            )
        return endpoint
