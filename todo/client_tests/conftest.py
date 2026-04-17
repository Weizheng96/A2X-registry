"""Shared fixtures for SDK unit tests.

Tests live under ``todo/client_tests/`` (intentionally outside ``src/client/``
so they never get packaged with the distributable SDK). Tests import the SDK
as ``src.client.*``.

Run from repo root with ``pytest todo/client_tests``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def ownership_path(tmp_path: Path) -> Path:
    return tmp_path / "owned.json"
