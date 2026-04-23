"""Runtime path resolution.

The library ships **without** bundled data. Two external locations must be
resolvable at runtime:

- ``database/`` — per-dataset service / taxonomy / query files
- ``llm_apikey.json`` — credentials for external LLM providers

Resolution order (first hit wins):

1. The ``A2X_REGISTRY_HOME`` environment variable, if set.
2. The current working directory, if it contains ``./database/`` **or**
   ``./llm_apikey.json`` (developer mode: running from a cloned source tree).
3. ``~/.a2x_registry/`` — default user-level home.

The chosen home directory is created on first write (by other code); this
module never creates it.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ENV_VAR = "A2X_REGISTRY_HOME"
DEFAULT_USER_HOME = Path.home() / ".a2x_registry"


@lru_cache(maxsize=1)
def get_home() -> Path:
    """Return the resolved runtime home directory (cached)."""
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "database").is_dir() or (cwd / "llm_apikey.json").is_file():
        return cwd.resolve()
    return DEFAULT_USER_HOME


def database_dir() -> Path:
    return get_home() / "database"


def dataset_dir(dataset: str) -> Path:
    return database_dir() / dataset


def llm_apikey_path() -> Path:
    return get_home() / "llm_apikey.json"


def reset_cache() -> None:
    """Clear the cached home lookup. Tests use this after setting ``A2X_REGISTRY_HOME``."""
    get_home.cache_clear()
