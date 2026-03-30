"""RegistryStore — per-dataset file I/O manager.

Manages user_config.json (read-only) + api_config.json (read-write) + service.json (write-only).
Keeps api_config in memory to avoid re-reading on every write.
"""

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .models import AgentCard, GenericServiceData, RegistryEntry

logger = logging.getLogger(__name__)

USER_CONFIG_FILE = "user_config.json"
API_CONFIG_FILE = "api_config.json"
SERVICE_JSON_FILE = "service.json"


class RegistryStore:
    """Thread-safe file I/O for a single dataset directory."""

    def __init__(self, dataset_dir: Path):
        self._dir = dataset_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._api_entries: Dict[str, RegistryEntry] = {}
        self._lock = threading.Lock()

    # --- Read ---

    def load_user_config(self) -> List[RegistryEntry]:
        """Read user_config.json (user-maintained, system read-only)."""
        path = self._dir / USER_CONFIG_FILE
        if not path.exists():
            return []
        entries = _load_config_file(path, source="user_config")
        if not entries and path.stat().st_size > 10:
            logger.error("user_config.json exists but produced 0 entries: %s", path)
        return entries

    def load_api_config(self) -> List[RegistryEntry]:
        """Read api_config.json and cache in memory."""
        path = self._dir / API_CONFIG_FILE
        entries = _load_config_file(path, source="api_config")
        with self._lock:
            self._api_entries = {e.service_id: e for e in entries}
        return entries

    # --- Write api_config ---

    def save_api_entry(self, entry: RegistryEntry):
        """Upsert one entry into api_config.json (memory + disk)."""
        with self._lock:
            self._api_entries[entry.service_id] = entry
            self._flush_api_config()

    def remove_api_entry(self, service_id: str) -> bool:
        """Remove one entry from api_config.json. Returns True if found."""
        with self._lock:
            if service_id not in self._api_entries:
                return False
            del self._api_entries[service_id]
            self._flush_api_config()
            return True

    def save_api_batch(self, entries: List[RegistryEntry]):
        """Replace all api_config entries and write to disk."""
        with self._lock:
            self._api_entries = {e.service_id: e for e in entries}
            self._flush_api_config()

    def _flush_api_config(self):
        """Serialize _api_entries to api_config.json. Must be called with lock held."""
        services = [_entry_to_config_dict(e) for e in self._api_entries.values()]
        _atomic_write(self._dir / API_CONFIG_FILE, {"services": services})

    # --- Write service.json ---

    def write_service_json(self, services: List[dict]):
        """Atomically write the output service.json."""
        _atomic_write(self._dir / SERVICE_JSON_FILE, services)


# ---------------------------------------------------------------------------
# Module-level utilities (no instance state, reusable)
# ---------------------------------------------------------------------------

def generate_service_id(type_prefix: str, name: str) -> str:
    """Generate a deterministic service ID from type prefix and name.

    Uses 16 hex chars (64 bits) to avoid birthday-paradox collisions.
    """
    h = hashlib.sha256(name.encode()).hexdigest()[:16]
    return f"{type_prefix}_{h}"


def _load_config_file(path: Path, source: str) -> List[RegistryEntry]:
    """Parse a config file (user_config or api_config format) into entries."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load %s: %s", path, e)
        return []

    entries = []
    for svc in data.get("services", []):
        try:
            entry = _parse_service_entry(svc, source)
            if entry:
                entries.append(entry)
        except Exception as e:
            logger.warning("Skipping invalid entry in %s: %s — %s", path, svc.get("service_id", "?"), e)
    return entries


def _parse_service_entry(svc: dict, source: str) -> Optional[RegistryEntry]:
    """Parse a single service dict from config into a RegistryEntry."""
    svc_type = svc.get("type", "generic")
    service_id = svc.get("service_id", "")

    if svc_type == "generic":
        name = (svc.get("name") or "").strip()
        desc = (svc.get("description") or "").strip()
        if not name or not desc:
            return None
        if not service_id:
            service_id = generate_service_id("generic", name)
        return RegistryEntry(
            service_id=service_id,
            type="generic",
            source=source,
            service_data=GenericServiceData(
                name=name, description=desc,
                inputSchema=svc.get("inputSchema", {}),
                url=svc.get("url"),
            ),
        )
    elif svc_type == "a2a":
        card_data = svc.get("agent_card")
        card_url = svc.get("agent_card_url")
        agent_card = AgentCard(**card_data) if card_data else None
        if not service_id:
            name = agent_card.name if agent_card else (card_url or "unknown")
            service_id = generate_service_id("agent", name)
        return RegistryEntry(
            service_id=service_id, type="a2a", source=source,
            agent_card=agent_card, agent_card_url=card_url,
        )
    return None


def _entry_to_config_dict(entry: RegistryEntry) -> dict:
    """Convert a RegistryEntry back to the config file dict format."""
    d: dict = {"type": entry.type, "service_id": entry.service_id}
    if entry.type == "generic" and entry.service_data:
        d["name"] = entry.service_data.name
        d["description"] = entry.service_data.description
        if entry.service_data.inputSchema:
            d["inputSchema"] = entry.service_data.inputSchema
        if entry.service_data.url:
            d["url"] = entry.service_data.url
    elif entry.type == "a2a":
        if entry.agent_card_url:
            d["agent_card_url"] = entry.agent_card_url
        if entry.agent_card:
            d["agent_card"] = entry.agent_card.model_dump(exclude_defaults=True)
    return d


def _atomic_write(path: Path, data):
    """Write JSON data atomically via temp file + os.replace."""
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.replace(str(tmp_path), str(path))
    except OSError:
        import time
        time.sleep(0.05)
        try:
            os.replace(str(tmp_path), str(path))
        except OSError:
            # Fallback: direct overwrite (non-atomic but functional on Windows)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
