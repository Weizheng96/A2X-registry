"""FastAPI router for cluster endpoints (``/api/cluster/*``).

Every route depends on ``require_cluster_store`` so the whole surface
returns 404 when the cluster module isn't initialized.

M0 exposes only ``GET /state`` (observability). The peer/session/sync
endpoints are added in subsequent milestones.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from .deps import require_cluster_store
from .store import ClusterStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cluster", tags=["cluster"])


@router.get("/state")
async def get_state(store: ClusterStore = Depends(require_cluster_store)):
    """Return this instance's node id and a snapshot of sync state."""
    return store.state_summary()
