"""FastAPI router for cluster endpoints (``/api/cluster/*``), RESTful.

Every route depends on ``require_cluster_store`` so the whole surface
returns 404 when the cluster module isn't initialized. Handlers are thin:
they delegate to ``ClusterStore`` methods (the same methods the in-process
test transport calls), keeping the HTTP layer free of sync logic.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .deps import require_cluster_store
from .store import ClusterStore
from .transport import TransportError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cluster", tags=["cluster"])


# ── request models ───────────────────────────────────────────────────────

class AddPeerRequest(BaseModel):
    address: str
    namespaces: Optional[List[str]] = None
    token: Optional[str] = None


class OpenRequest(BaseModel):
    node_id: str
    address: str = ""
    namespaces: List[str] = []
    token: Optional[str] = None


class PullRequest(BaseModel):
    from_node: str
    keys: List[list]


class UpdatesRequest(BaseModel):
    from_node: str
    envelopes: List[dict]


# ── trigger / session management ─────────────────────────────────────────

@router.post("/peers")
async def add_peer(req: AddPeerRequest, store: ClusterStore = Depends(require_cluster_store)):
    """Discover-and-connect trigger: open a session with the peer at
    ``address`` and run an initial reconcile. Called by the local CLI
    (``cluster add-peer``) or the link-layer daemon."""
    try:
        peer = store.connect_peer(req.address, req.namespaces, req.token)
    except TransportError as exc:
        raise HTTPException(status_code=502, detail=f"peer unreachable: {exc}")
    return {"peer": peer.to_summary()}


@router.get("/peers")
async def list_peers(store: ClusterStore = Depends(require_cluster_store)):
    return {"peers": store.state_summary()["peers"]}


@router.delete("/peers/{node_id}")
async def remove_peer(node_id: str, store: ClusterStore = Depends(require_cluster_store)):
    removed = store.disconnect_peer(node_id)
    return {"node_id": node_id, "removed": removed}


# ── peer-facing sync endpoints ───────────────────────────────────────────

@router.post("/sessions")
async def open_session(req: OpenRequest, store: ClusterStore = Depends(require_cluster_store)):
    """Receive an OPEN handshake from a peer (per-namespace authorization)."""
    return store.handle_open(req.model_dump())


@router.get("/digest")
async def get_digest(
    from_node: str = Query(...),
    namespaces: str = Query(""),
    store: ClusterStore = Depends(require_cluster_store),
):
    ns = [n for n in namespaces.split(",") if n] if namespaces else None
    return store.serve_digest(from_node, ns)


@router.post("/pulls")
async def post_pulls(req: PullRequest, store: ClusterStore = Depends(require_cluster_store)):
    return store.serve_pull(req.from_node, req.keys)


@router.post("/updates")
async def post_updates(req: UpdatesRequest, store: ClusterStore = Depends(require_cluster_store)):
    return store.serve_updates(req.from_node, req.envelopes)


# ── observability ────────────────────────────────────────────────────────

@router.get("/state")
async def get_state(store: ClusterStore = Depends(require_cluster_store)):
    """Return this instance's node id and a snapshot of sync state."""
    return store.state_summary()
