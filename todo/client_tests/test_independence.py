"""SDK independence regression tests (X-DEP-*).

The SDK must be packageable standalone: no imports of other project modules,
and no dependencies outside ``httpx`` + Python stdlib. These tests run on
every CI round so accidental coupling fails fast.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parents[2] / "src" / "client"

# First-party allowlist: only relative imports inside the client package.
ALLOWED_THIRD_PARTY = {"httpx"}

# Python 3.10+ exposes the authoritative list of stdlib top-level modules.
STDLIB = set(sys.stdlib_module_names)


def _iter_client_py_files():
    return [p for p in CLIENT_DIR.glob("*.py")]


def _collect_imports(path: Path) -> set[str]:
    """Top-level module names imported by the file (no submodule tail)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


def test_no_internal_project_imports():
    """X-DEP-001: no `from src.xxx` unless it is `from src.client.xxx`."""
    offenders = []
    for path in _iter_client_py_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("from src.") and not stripped.startswith("from src.client"):
                offenders.append(f"{path.name}:{lineno} {stripped}")
            if stripped.startswith("import src.") and not stripped.startswith("import src.client"):
                offenders.append(f"{path.name}:{lineno} {stripped}")
    assert offenders == [], f"Illegal internal imports: {offenders}"


def test_only_httpx_and_stdlib():
    """X-DEP-002: third-party deps must be {httpx} only."""
    seen_third_party = set()
    for path in _iter_client_py_files():
        for mod in _collect_imports(path):
            if not mod or mod == "src":
                continue
            if mod in STDLIB:
                continue
            if mod not in ALLOWED_THIRD_PARTY:
                seen_third_party.add(mod)

    extras = seen_third_party - ALLOWED_THIRD_PARTY
    assert extras == set(), (
        f"Unexpected third-party dependencies: {extras}. "
        f"Allowed: {ALLOWED_THIRD_PARTY}"
    )


def test_py_typed_marker_present():
    """PEP 561: ``py.typed`` must be in the package root."""
    assert (CLIENT_DIR / "py.typed").exists()


def test_pyproject_lists_only_a2x_client_package():
    """Sanity: pyproject exports only the SDK package, not tests."""
    content = (CLIENT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["a2x_client"]' in content
