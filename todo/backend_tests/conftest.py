"""Shared fixtures for backend API tests.

Each test gets a fresh temp ``database/`` dir and a ``TestClient`` pointed
at the FastAPI app. The RegistryService is re-initialised per-test so the
state (datasets, services) doesn't leak between tests.

Run from repo root with ``pytest todo/backend_tests``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient bound to a FastAPI app with a temp RegistryService."""
    from fastapi import FastAPI
    from src.backend.routers import dataset as dataset_router

    # Fresh service rooted at tmp_path/database — isolated from the real repo
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    dataset_router.init_registry_service(db_dir)

    # Minimal app — only the dataset router (enough for filter-mode tests)
    app = FastAPI()
    app.include_router(dataset_router.router)

    with TestClient(app) as tc:
        yield tc
