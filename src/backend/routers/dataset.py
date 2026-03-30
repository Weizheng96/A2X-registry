"""Dataset management API — CRUD, services, taxonomy, embedding config."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.backend.schemas.models import DatasetInfo, DefaultQuery
from src.backend.services.search_service import search_service
from src.backend.services.taxonomy_service import get_taxonomy_tree
from src.backend.default_queries import get_default_queries
from src.register.models import (
    RegisterGenericRequest, RegisterA2ARequest,
    RegisterResponse, DeregisterResponse,
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
    """Run a blocking function in the thread pool, mapping exceptions to HTTP errors."""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, fn, *args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


@router.post("")
async def create_dataset(req: CreateDatasetRequest):
    """Create a new empty dataset directory with embedding config."""
    svc = get_registry_service()
    await _run(svc.create_dataset, req.name, req.embedding_model)
    return {"dataset": req.name, "embedding_model": req.embedding_model, "status": "created"}


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

@router.get("/{dataset}/services")
async def list_services(
    dataset: str,
    mode: str = Query("browse", description="browse | admin | full"),
    size: int = Query(-1, ge=-1),
    page: int = Query(1, ge=1),
):
    """List services in a dataset.

    Modes:
      browse — lightweight: [{id, name, description}]
      admin  — with type/source: [{id, name, description, type, source}]
      full   — paginated full metadata (for admin panel list op)
    """
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


@router.delete("/{dataset}/services/{service_id}", response_model=DeregisterResponse)
async def deregister(dataset: str, service_id: str):
    """Deregister a service."""
    return await _run(get_registry_service().deregister, dataset, service_id)


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
