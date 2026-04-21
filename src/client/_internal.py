"""Shared helpers used by the sync and async client classes.

Keeping these as pure module-level functions lets ``A2XClient`` and
``AsyncA2XClient`` stay symmetric without sharing a base class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx

from .errors import UnexpectedServiceTypeError
from .models import AgentDetail

# ── Sentinels / constants ────────────────────────────────────────────────────

UNSET: Final[Any] = object()
"""Sentinel distinguishing "argument omitted" from "explicitly None"."""

DEFAULT_FORMATS: Final[dict[str, str]] = {"a2a": "v0.0"}
"""SDK default ``formats`` for Agent Team use cases."""

DEFAULT_OWNERSHIP_FILE: Final[Path] = Path.home() / ".a2x_client" / "owned.json"

TEAM_COUNT_FIELD: Final[str] = "agentTeamCount"

BLANK_AGENT_NAME_PREFIX: Final[str] = "_BlankAgent_"
"""Name prefix used when constructing a blank card. Kept distinct so two
blank agents with different endpoints get different ``name``s (and thus
different sids via ``generate_service_id``); no longer used for discovery."""

BLANK_DESCRIPTION_SENTINEL: Final[str] = "__BLANK__"
"""Description sentinel identifying idle-pool agents. Matched exactly via
``mode=filter`` against the **raw** agent_card.description (pre-
build_description transform). Changing it breaks cross-SDK interop."""

ENDPOINT_FIELD: Final[str] = "endpoint"
"""Custom AgentCard field holding the agent's endpoint URL. AgentCard uses
``extra="allow"`` (see ``src/register/models.py``), so the backend stores it
verbatim. Callers of ``replace_agent_card`` must preserve this field so
``restore_to_blank`` can recover the endpoint after process restart."""

_CONTENT_TYPE_JSON: Final[str] = "application/json"


# ── URL construction ─────────────────────────────────────────────────────────

def _encode(segment: str) -> str:
    return quote(segment, safe="")


# Paths are relative (no leading "/") so ``httpx.Client(base_url=...)`` joins
# them under any mount point (e.g. ``http://host/a2x/``). Callers must ensure
# ``base_url`` ends with ``/`` — ``normalize_base_url`` takes care of that.

DATASETS_ROOT = "api/datasets"


def dataset_path(dataset: str) -> str:
    return f"{DATASETS_ROOT}/{_encode(dataset)}"


def services_path(dataset: str) -> str:
    return f"{DATASETS_ROOT}/{_encode(dataset)}/services"


def service_path(dataset: str, service_id: str) -> str:
    return f"{DATASETS_ROOT}/{_encode(dataset)}/services/{_encode(service_id)}"


def a2a_register_path(dataset: str) -> str:
    return f"{DATASETS_ROOT}/{_encode(dataset)}/services/a2a"


def normalize_base_url(base_url: str) -> str:
    """Ensure trailing ``/`` so relative paths append under the mount point."""
    return base_url if base_url.endswith("/") else base_url + "/"


# ── Body construction ────────────────────────────────────────────────────────

def build_create_dataset_body(
    name: str,
    embedding_model: str,
    formats: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "embedding_model": embedding_model}
    if formats is UNSET:
        body["formats"] = dict(DEFAULT_FORMATS)
    elif formats is not None:
        body["formats"] = formats
    return body


def build_register_agent_body(
    agent_card: dict[str, Any],
    service_id: str | None,
    persistent: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {"agent_card": agent_card, "persistent": persistent}
    if service_id is not None:
        body["service_id"] = service_id
    return body


def build_team_count_body(count: int) -> dict[str, Any]:
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(f"count must be a non-negative int, got {count!r}")
    return {TEAM_COUNT_FIELD: count}


def build_blank_agent_card(endpoint: str) -> dict[str, Any]:
    """Blank-agent AgentCard template.

    ``name`` encodes the endpoint so the deterministic
    ``generate_service_id("agent", name)`` on the backend keeps sid stable
    across re-registrations of the same endpoint.

    ``description`` carries the ``BLANK_DESCRIPTION_SENTINEL`` — this is
    what ``mode=filter`` matches to discover idle-pool agents.
    """
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError(f"endpoint must be a non-empty string, got {endpoint!r}")
    return {
        "name": f"{BLANK_AGENT_NAME_PREFIX}{endpoint}",
        "description": BLANK_DESCRIPTION_SENTINEL,
        ENDPOINT_FIELD: endpoint,
        TEAM_COUNT_FIELD: 0,
    }


def build_filter_params(filters: dict[str, Any]) -> dict[str, Any]:
    """Build query params for ``GET .../services?mode=filter&...``.

    Every ``(k, v)`` in ``filters`` becomes a query param. Values are
    coerced to strings (HTTP query params are strings; backend also
    string-coerces its comparison). Empty filter dict raises locally.
    """
    if not isinstance(filters, dict) or not filters:
        raise ValueError(
            f"filters must be a non-empty dict, got {filters!r}"
        )
    reserved = {"mode", "service_id", "size", "page"}
    params: dict[str, Any] = {"mode": "filter"}
    for k, v in filters.items():
        if not isinstance(k, str) or not k:
            raise ValueError(f"filter keys must be non-empty strings, got {k!r}")
        if k in reserved:
            raise ValueError(
                f"filter key {k!r} collides with a reserved query param "
                f"({reserved}); backend would drop it before filtering"
            )
        if v is None:
            raise ValueError(f"filter value for {k!r} must not be None")
        params[k] = str(v)
    return params


def build_full_list_params(page: int, size: int) -> dict[str, Any]:
    """Build query params for ``GET .../services?mode=full``.

    ``size=-1`` returns the full dataset in one page (backend default);
    ``size>=1`` paginates with ``page`` (1-indexed).
    """
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(f"page must be >= 1, got {page!r}")
    if not isinstance(size, int) or isinstance(size, bool) or (size < -1 or size == 0):
        raise ValueError(f"size must be -1 or >= 1, got {size!r}")
    return {"mode": "full", "page": page, "size": size}


def extract_team_count(card: Any) -> int:
    """Read ``agentTeamCount`` from an AgentCard dict.

    Defaults to 0 (most-idle) for missing/invalid values, matching the
    blank-agent invariant that idle agents always have count=0.
    """
    if not isinstance(card, dict):
        return 0
    count = card.get(TEAM_COUNT_FIELD, 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return 0
    return count


def extract_endpoint(card: Any) -> str | None:
    if not isinstance(card, dict):
        return None
    value = card.get(ENDPOINT_FIELD)
    if isinstance(value, str) and value.strip():
        return value
    return None


def assert_card_has_endpoint(agent_card: Any) -> None:
    """Fail-fast guard for ``replace_agent_card``.

    The ``endpoint`` field is load-bearing: ``restore_to_blank``'s L2 fallback
    reads it back from the current card across process restarts. A
    ``replace_agent_card`` call that drops it would silently block future
    restores. Raising locally (no HTTP) gives callers a clear signal before
    the registry is mutated.
    """
    if extract_endpoint(agent_card) is None:
        raise ValueError(
            "agent_card must include a non-empty 'endpoint' field; it is "
            "required so restore_to_blank can recover the endpoint later. "
            f"Got card: {agent_card!r}"
        )


# ── Ownership-file resolution ────────────────────────────────────────────────

def resolve_ownership_file(raw: Any) -> Path | None:
    """Return the effective ownership-file path, or ``None`` for memory-only mode.

    - ``None`` → default ``~/.a2x_client/owned.json``
    - ``False`` → disable persistence
    - ``Path`` / ``str`` → use as-is
    """
    if raw is None:
        return DEFAULT_OWNERSHIP_FILE
    if raw is False:
        return None
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    raise TypeError(
        f"ownership_file must be None, False, Path, or str; got {type(raw).__name__}"
    )


def build_default_headers(api_key: str | None) -> dict[str, str] | None:
    if not api_key:
        return None
    return {"Authorization": f"Bearer {api_key}"}


# ── Response post-processing ─────────────────────────────────────────────────

def parse_agent_detail(resp: httpx.Response) -> AgentDetail:
    """Decode a ``mode=single`` response or raise ``UnexpectedServiceTypeError``."""
    content_type = resp.headers.get("content-type", "")
    if _CONTENT_TYPE_JSON not in content_type.lower():
        raise UnexpectedServiceTypeError(
            f"expected application/json, got {content_type or '<unknown>'}",
            status_code=resp.status_code,
            payload=None,
        )
    data = resp.json()
    if not isinstance(data, dict):
        raise UnexpectedServiceTypeError(
            f"expected JSON object for agent detail, got {type(data).__name__}",
            status_code=resp.status_code,
            payload=None,
        )
    return AgentDetail.from_dict(data)


def parse_full_list_servers(resp: httpx.Response) -> list[dict[str, Any]]:
    """Extract the ``servers`` array from a ``mode=full`` response.

    Backend quirk: for a2a entries, each item is the raw Agent Card
    (no ``id`` wrapper); for generic/skill, items have the standard
    ``{id, type, name, description, metadata}`` shape. Callers that need
    sids for a2a must join with a ``mode=browse`` call by name.
    """
    data = resp.json()
    if not isinstance(data, dict):
        return []
    servers = data.get("servers", [])
    return [s for s in servers if isinstance(s, dict)]
