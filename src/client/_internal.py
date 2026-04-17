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
