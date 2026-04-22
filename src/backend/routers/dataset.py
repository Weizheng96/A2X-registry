"""Dataset management API — CRUD, services, taxonomy, embedding config."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from src.backend.schemas.models import DatasetInfo, DefaultQuery
from src.backend.services.search_service import search_service
from src.backend.services.taxonomy_service import get_taxonomy_tree
from src.backend.default_queries import get_default_queries
from src.register.errors import RegistryNotFoundError
from src.register.models import (
    RegisterGenericRequest, RegisterA2ARequest,
    RegisterResponse, DeregisterResponse, SkillResponse, UpdateResponse,
)
from src.register.service import RegistryService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/datasets", tags=["datasets"])

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_service: Optional[RegistryService] = None
_executor = ThreadPoolExecutor(max_workers=2)


def init_registry_service(database_dir: Path, global_config_path: Optional[Path] = None):
    """Initialize the global RegistryService. Called once from backend startup."""
    global _service
    _service = RegistryService(database_dir, global_config_path)
    return _service


def get_registry_service() -> RegistryService:
    """Return the registry service or raise 503."""
    if _service is None:
        raise HTTPException(status_code=503, detail="Registry service not initialized")
    return _service


async def _run(fn, *args):
    """Run a blocking function in the thread pool, mapping exceptions to HTTP errors.

    Layered error contract (see docs/client_design.md §3.4):
      RegistryNotFoundError → 404   (business "resource doesn't exist")
      ValueError            → 400   (validation / forbidden source)
      FileNotFoundError     → 404   (skill folder missing on disk)
      KeyError              → 404   (legacy fallback for any not-yet-migrated path)
    """
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, fn, *args)
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Registry error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Dataset CRUD ──────────────────────────────────────────────────────────────

@router.get("", response_model=list[DatasetInfo])
async def list_datasets():
    """List available datasets with their service and query counts."""
    db_dir = PROJECT_ROOT / "database"
    datasets = []
    for d in sorted(db_dir.iterdir()):
        service_file = d / "service.json"
        if not d.is_dir() or not service_file.exists():
            continue
        with open(service_file, encoding="utf-8") as f:
            svc_count = len(json.load(f))
        query_file = d / "query" / "query.json"
        q_count = 0
        if query_file.exists():
            with open(query_file, encoding="utf-8") as f:
                q_count = len(json.load(f))
        datasets.append(DatasetInfo(name=d.name, service_count=svc_count, query_count=q_count))
    return datasets


class CreateDatasetRequest(BaseModel):
    name: str
    embedding_model: str = "all-MiniLM-L6-v2"
    # Optional {type: min_version} declaring which registration formats this
    # dataset will accept. Missing/None → all three types allowed from v0.0.
    formats: Optional[dict] = None


@router.post("")
async def create_dataset(req: CreateDatasetRequest):
    """Create a new empty dataset directory with embedding + register-format config."""
    svc = get_registry_service()
    await _run(svc.create_dataset, req.name, req.embedding_model, req.formats)
    return {
        "dataset": req.name,
        "embedding_model": req.embedding_model,
        "formats": svc.get_register_config(req.name),
        "status": "created",
    }


# ── Registration format config ────────────────────────────────────────────────

class RegisterConfigRequest(BaseModel):
    formats: dict  # {type: min_version} or {type: {"min_version": "v0.0"}}


@router.get("/{dataset}/register-config")
async def get_register_config(dataset: str):
    """Return the per-type ``min_version`` map that gates registration."""
    svc = get_registry_service()
    return {"dataset": dataset, "formats": svc.get_register_config(dataset)}


@router.post("/{dataset}/register-config")
async def set_register_config(dataset: str, req: RegisterConfigRequest):
    """Replace the allowed registration formats for a dataset.

    Body: ``{"formats": {"generic": "v0.0", "a2a": "v1.0", "skill": "v0.0"}}``.
    Unknown types / versions are silently dropped. Empty result → 400.
    """
    svc = get_registry_service()
    cfg = await _run(svc.set_register_config, dataset, req.formats)
    return {"dataset": dataset, "formats": cfg}


@router.delete("/{dataset}")
async def delete_dataset(dataset: str):
    """Delete a dataset directory and all associated data."""
    svc = get_registry_service()
    # Clean ChromaDB collection
    try:
        from src.vector.utils.chroma_store import ChromaStore
        collection = dataset.lower().replace("-", "_")
        chroma_dir = str(PROJECT_ROOT / "database" / "chroma")
        store = ChromaStore(collection, chroma_dir)
        store.clear()
        logger.info("Cleared ChromaDB collection: %s", collection)
    except Exception as e:
        logger.warning("Failed to clear ChromaDB for %s: %s", dataset, e)
    # Invalidate cached search instances
    with search_service._lock:
        search_service._vector_instances.pop(dataset, None)
        for key in list(search_service._a2x_instances):
            if key.startswith(f"{dataset}_"):
                search_service._a2x_instances.pop(key, None)
        search_service._traditional_instances.pop(dataset, None)
    # Delete dataset directory
    await _run(svc.delete_dataset, dataset)
    return {"dataset": dataset, "status": "deleted"}


# ── Services (register / deregister / list) ───────────────────────────────────

_RESERVED_QUERY_PARAMS = frozenset({"mode", "service_id", "size", "page"})


def _entry_filter_dict(entry) -> Optional[dict]:
    """Type-specific 'raw' dict used for ``mode=filter`` matching.

    Returns the untransformed per-type data model — AgentCard for a2a
    (with ``extra=allow`` custom fields like ``endpoint``/``agentTeamCount``
    preserved), GenericServiceData for generic, SkillData for skill. This
    intentionally differs from the ``build_description``-transformed
    ``description`` exposed by ``list_services`` / browse / full, giving
    filter callers predictable equality semantics on what they wrote in.
    """
    if entry.type == "a2a" and entry.agent_card:
        return entry.agent_card.model_dump(exclude_none=True)
    if entry.type == "generic" and entry.service_data:
        return entry.service_data.model_dump()
    if entry.type == "skill" and entry.skill_data:
        return entry.skill_data.model_dump()
    return None


@router.get("/{dataset}/services")
async def list_services(
    dataset: str,
    request: Request,
    mode: str = Query("browse", description="browse | admin | full | single | filter"),
    service_id: Optional[str] = Query(None, description="Service ID (required for single mode)"),
    size: int = Query(-1, ge=-1),
    page: int = Query(1, ge=1),
):
    """List services in a dataset.

    Modes:
      browse — lightweight: [{id, name, description}]
      admin  — with type/source: [{id, name, description, type, source}]
      full   — paginated full metadata (for admin panel list op)
      single — query one service by ID; returns full entry (ZIP for skill type)
      filter — every non-reserved query param becomes a filter condition
               (AND semantics, string-coerced equality). Matches on each
               entry's type-specific raw dict (see _entry_filter_dict).
               Returns the standard [{id, type, name, description, metadata}]
               wrapper; missing fields → not a match. Empty filters → every
               entry matches (full dataset listing).
    """
    if mode == "single":
        if not service_id:
            raise HTTPException(status_code=400, detail="service_id is required for single mode")
        svc = get_registry_service()
        entry = svc.get_entry(dataset, service_id)
        if not entry:
            raise HTTPException(
                status_code=404,
                detail=f"Service '{service_id}' not found in dataset '{dataset}'",
            )
        # Skill type: return ZIP download
        if entry.type == "skill" and entry.skill_data:
            try:
                zip_bytes = svc.get_skill_zip(dataset, entry.skill_data.name)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Skill folder not found: {entry.skill_data.name}")
            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{entry.skill_data.name}.zip"'},
            )
        # Generic/A2A: return full service.json entry
        output = [s for s in svc.list_services(dataset) if s["id"] == service_id]
        return output[0] if output else None

    if mode == "filter":
        filters = {
            k: v for k, v in request.query_params.items()
            if k not in _RESERVED_QUERY_PARAMS
        }
        # Empty filters → every entry matches (all(...) over {} is True),
        # i.e. return the whole dataset in the wrapped shape. This lets
        # clients use mode=filter as the single "list-or-filter" endpoint.
        svc = get_registry_service()
        wrapped_by_id = {s["id"]: s for s in svc.list_services(dataset)}
        output = []
        for entry in svc.list_entries(dataset):
            match = _entry_filter_dict(entry)
            if match is None:
                continue
            if all(k in match and str(match[k]) == v for k, v in filters.items()):
                wrapped = wrapped_by_id.get(entry.service_id)
                if wrapped is not None:
                    output.append(wrapped)
        return output

    if mode == "browse":
        service_file = PROJECT_ROOT / "database" / dataset / "service.json"
        if not service_file.exists():
            return []
        with open(service_file, encoding="utf-8") as f:
            services = json.load(f)
        return [{"id": s["id"], "name": s["name"], "description": s.get("description", "")}
                for s in services]

    svc = get_registry_service()

    if mode == "admin":
        entries = []
        for e in sorted(svc.list_entries(dataset), key=lambda e: e.service_id):
            if e.type == "generic" and e.service_data:
                name, description = e.service_data.name, e.service_data.description
            elif e.type == "skill" and e.skill_data:
                name, description = e.skill_data.name, e.skill_data.description
            elif e.agent_card:
                name, description = e.agent_card.name, e.agent_card.description
            else:
                name, description = e.service_id, ""
            entries.append({"id": e.service_id, "type": e.type, "name": name,
                            "description": description, "source": e.source})
        return entries

    # mode == "full" — paginated
    all_entries = sorted(svc.list_services(dataset), key=lambda e: e["id"])
    total = len(all_entries)

    if size == -1:
        page_entries = all_entries
    else:
        offset = (page - 1) * size
        page_entries = all_entries[offset: offset + size]

    servers = [e["metadata"] if e.get("type") == "a2a" else e for e in page_entries]

    if size == -1:
        current_page, total_pages = 1, 1
    else:
        current_page = page
        total_pages = max(1, (total + size - 1) // size)

    return {
        "servers": servers,
        "metadata": {
            "count": len(servers), "total": total,
            "page": current_page, "total_pages": total_pages, "size": size,
        },
    }


@router.post("/{dataset}/services/generic", response_model=RegisterResponse)
async def register_generic(dataset: str, req: RegisterGenericRequest):
    """Register a generic service."""
    req.dataset = dataset
    return await _run(get_registry_service().register_generic, req)


@router.post("/{dataset}/services/a2a", response_model=RegisterResponse)
async def register_a2a(dataset: str, req: RegisterA2ARequest):
    """Register an A2A agent."""
    req.dataset = dataset
    return await _run(get_registry_service().register_a2a, req)


@router.put("/{dataset}/services/{service_id}", response_model=UpdateResponse)
async def update_service(dataset: str, service_id: str, updates: dict):
    """Partially update a service by top-level field upsert.

    Body is an arbitrary ``{field: value}`` dict. Fields not present are
    untouched; matching fields are replaced. No format validation (since
    fields are only added/replaced, not removed). Changing ``name`` or
    ``description`` marks the taxonomy as stale.

    Rejected:
      - user_config-sourced entries (edit user_config.json directly)
      - unknown fields for generic / skill types (strict schema)
      - skill rename whose target folder already exists
    """
    return await _run(get_registry_service().update_service,
                      dataset, service_id, updates)


@router.delete("/{dataset}/services/{service_id}", response_model=DeregisterResponse)
async def deregister(dataset: str, service_id: str):
    """Deregister a service."""
    return await _run(get_registry_service().deregister, dataset, service_id)


# ── Skills (register / download / delete) ─────────────────────────────────────

@router.post("/{dataset}/skills", response_model=SkillResponse)
async def upload_skill(dataset: str, file: UploadFile = File(...)):
    """Upload a skill as a ZIP file. ZIP must contain SKILL.md with valid frontmatter."""
    zip_bytes = await file.read()
    svc = get_registry_service()
    return await _run(svc.register_skill, dataset, zip_bytes)


@router.delete("/{dataset}/skills/{name}", response_model=SkillResponse)
async def delete_skill(dataset: str, name: str):
    """Delete a skill and its registry entry."""
    return await _run(get_registry_service().deregister_skill, dataset, name)


@router.get("/{dataset}/skills/{name}/download")
async def download_skill(dataset: str, name: str):
    """Download a skill folder as a ZIP file."""
    svc = get_registry_service()
    try:
        zip_bytes = svc.get_skill_zip(dataset, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


# ── Taxonomy ──────────────────────────────────────────────────────────────────

@router.get("/{dataset}/taxonomy")
async def get_taxonomy(dataset: str):
    """Return taxonomy tree structure for D3.js visualization."""
    try:
        return get_taxonomy_tree(dataset)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No taxonomy for dataset '{dataset}'")


# ── Default queries ───────────────────────────────────────────────────────────

@router.get("/{dataset}/default-queries")
async def default_queries(dataset: str):
    """Return all default queries for the given dataset in original order.

    Response includes a ``source`` field so the frontend can detect when two
    datasets share the same query pool and skip unnecessary reloads.
    """
    queries, source = get_default_queries(dataset)
    return {"source": source, "queries": [DefaultQuery(**q) for q in queries]}


# ── Embedding / vector config ─────────────────────────────────────────────────

@router.get("/embedding-models")
async def list_embedding_models():
    """Return the list of supported embedding models."""
    from src.vector.utils.embedding import EMBEDDING_MODELS
    return {"models": EMBEDDING_MODELS}


@router.get("/{dataset}/vector-config")
async def get_vector_config(dataset: str):
    """Get the vector (embedding) config for a dataset."""
    from src.vector.utils.embedding import DEFAULT_EMBEDDING_MODEL, EMBEDDING_MODELS
    config_path = PROJECT_ROOT / "database" / dataset / "vector_config.json"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {"embedding_model": DEFAULT_EMBEDDING_MODEL,
               "embedding_dim": EMBEDDING_MODELS[DEFAULT_EMBEDDING_MODEL]["dim"]}
    return {"dataset": dataset, **cfg}


@router.post("/{dataset}/vector-config")
async def set_vector_config(dataset: str, body: dict):
    """Set the embedding model for a dataset. Triggers vector index rebuild."""
    from src.vector.utils.embedding import DEFAULT_EMBEDDING_MODEL, EMBEDDING_MODELS

    model_name = body.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    info = EMBEDDING_MODELS.get(model_name)
    dim = info["dim"] if info else body.get("embedding_dim")
    if dim is None:
        raise HTTPException(status_code=400,
                            detail=f"Unknown model '{model_name}'; provide embedding_dim")

    config_path = PROJECT_ROOT / "database" / dataset / "vector_config.json"
    cfg = {"embedding_model": model_name, "embedding_dim": dim}
    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    search_service.schedule_vector_sync(dataset)

    return {"dataset": dataset, **cfg, "message": "配置已保存，向量索引将在后台重建"}
