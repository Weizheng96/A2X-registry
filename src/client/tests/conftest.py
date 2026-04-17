"""Shared fixtures for SDK unit tests.

Tests live under ``src/client/tests/`` so they ship next to the code, but they
are not included in the built wheel (pyproject only lists ``a2x_client``).
Run from repo root with ``pytest src/client/tests``.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def ownership_path(tmp_path: Path) -> Path:
    return tmp_path / "owned.json"
