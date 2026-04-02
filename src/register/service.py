"""RegistryService — multi-dataset business logic orchestrator.

Maintains an in-memory merged view per dataset. service.json is pure output.
All mutable state (_entries, _output_cache, _taxonomy_states) is protected by _lock.
"""

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set

from .agent_card import build_description, fetch_agent_card
from .models import (
    AgentCard,
    DeregisterResponse,
    GenericServiceData,
    RegisterA2ARequest,
    RegisterGenericRequest,
    RegisterResponse,
    RegistryEntry,
    RegistryStatus,
    SkillResponse,
    TaxonomyState,
)
from .store import RegistryStore, generate_service_id
from .validation import DEFAULT_ALLOWED_VERSIONS, validate_agent_card

logger = logging.getLogger(__name__)

USER_CONFIG_FILE = "user_config.json"
API_CONFIG_FILE  = "api_config.json"
BUILD_CONFIG_FILE = "build_config.json"
TAXONOMY_FILE    = "taxonomy.json"


class RegistryService:
    """Multi-dataset registry service.

    Thread-safety: a single _lock protects all mutable state
    (_entries, _output_cache, _taxonomy_states). File I/O is delegated to
    RegistryStore which has its own lock for api_config writes.
    """

    def __init__(
        self,
        database_dir: Path,
        global_config_path: Optional[Path] = None,
        allowed_a2a_versions: Optional[Set[str]] = None,
    ):
        self._database_dir = database_dir
        self._global_config_path = global_config_path
        self._allowed_a2a_versions = allowed_a2a_versions or DEFAULT_ALLOWED_VERSIONS
        self._stores: Dict[str, RegistryStore] = {}
        # Protected by _lock:
        self._entries: Dict[str, Dict[str, RegistryEntry]] = {}
        self._output_cache: Dict[str, List[dict]] = {}
        self._taxonomy_states: Dict[str, TaxonomyState] = {}
        self._lock = threading.Lock()
        # Optional callback: called with dataset name whenever service.json content changes.
        # Used by SearchService to keep the vector index in sync.
        self._on_service_changed = None

    # -----------------------------------------------------------------------
    # Startup
    # -----------------------------------------------------------------------

    def startup(self) -> Dict[str, TaxonomyState]:
        """Initialize all datasets. Returns {dataset: TaxonomyState}.

        Startup is single-threaded (called once from app.py), so no lock
        contention. We still acquire _lock when writing shared state.
        """
        if self._global_config_path and self._global_config_path.exists():
            self._distribute_global_config()

        datasets = self._discover_datasets()
        logger.info("Discovered %d datasets with registration config: %s", len(datasets), datasets)

        # Phase 1: Load config files and collect URL entries to fetch
        url_entries: List[tuple] = []  # [(dataset, entry), ...]
        for dataset in datasets:
            store = self._get_store(dataset)
            user_entries = store.load_user_config()
            api_entries = store.load_api_config()

            # Merge: api overrides user on same ID; validate A2A entries
            merged = {}
            for e in user_entries + api_entries:
                if e.type == "a2a" and e.agent_card:
                    vr = validate_agent_card(e.agent_card, self._allowed_a2a_versions)
                    if not vr.valid:
                        logger.warning("Skipping invalid A2A '%s' in %s: %s",
                                       e.service_id, dataset, "; ".join(vr.errors))
                        continue
                merged[e.service_id] = e
                if e.agent_card_url:
                    url_entries.append((dataset, e))

            # Load skill folders (lowest priority — don't override user_config/api_config)
            skill_entries = store.load_skills()
            for e in skill_entries:
                if e.service_id not in merged:
                    merged[e.service_id] = e

            with self._lock:
                self._entries[dataset] = merged

        # Phase 2: Parallel fetch agent_card_urls
        if url_entries:
            self._fetch_agent_cards_parallel(url_entries)

        # Phase 3: Generate output and compute taxonomy states
        result = {}
        for dataset in datasets:
            store = self._get_store(dataset)

            # Persist fresh agent_card snapshots
            with self._lock:
                api_entries = [e for e in self._entries[dataset].values() if e.source == "api_config"]
            if api_entries:
                store.save_api_batch(api_entries)

            self._regenerate_output(dataset)
            state = self._init_taxonomy_state(dataset)
            result[dataset] = state

            count = len(self._output_cache.get(dataset, []))
            logger.info("Dataset '%s': %d services, taxonomy=%s", dataset, count, state.value)

        return result

    # -----------------------------------------------------------------------
    # Register
    # -----------------------------------------------------------------------

    def register_generic(self, req: RegisterGenericRequest) -> RegisterResponse:
        """Register a generic service."""
        if not (req.name and req.name.strip()):
            raise ValueError("name is required and cannot be empty")
        if not (req.description and req.description.strip()):
            raise ValueError("description is required and cannot be empty")

        dataset = req.dataset
        service_id = req.service_id or generate_service_id("generic", req.name)

        entry = RegistryEntry(
            service_id=service_id, type="generic",
            source="api_config" if req.persistent else "ephemeral",
            service_data=GenericServiceData(
                name=req.name, description=req.description,
                inputSchema=req.inputSchema, url=req.url,
            ),
        )
        return self._do_register(dataset, entry, req.persistent)

    def register_a2a(self, req: RegisterA2ARequest) -> RegisterResponse:
        """Register an A2A agent (full card or URL)."""
        if req.agent_card:
            agent_card = req.agent_card
            agent_card_url = None
        elif req.agent_card_url:
            agent_card = fetch_agent_card(req.agent_card_url)
            agent_card_url = req.agent_card_url
        else:
            raise ValueError("Either agent_card or agent_card_url must be provided")

        self._validate_a2a_card(agent_card)

        dataset = req.dataset
        service_id = req.service_id or generate_service_id("agent", agent_card.name)

        entry = RegistryEntry(
            service_id=service_id, type="a2a",
            source="api_config" if req.persistent else "ephemeral",
            agent_card=agent_card, agent_card_url=agent_card_url,
        )
        return self._do_register(dataset, entry, req.persistent)

    def register_batch(self, entries: List[RegistryEntry], dataset: str, persistent: bool = True):
        """Register multiple entries at once (single file write)."""
        source = "api_config" if persistent else "ephemeral"
        with self._lock:
            ds = self._entries.setdefault(dataset, {})
            for e in entries:
                copy = e.model_copy(update={"source": source})
                ds[copy.service_id] = copy
            if persistent:
                all_api = [e for e in ds.values() if e.source == "api_config"]

        if persistent and all_api:
            self._get_store(dataset).save_api_batch(all_api)
        self._regenerate_output(dataset)
        self._mark_taxonomy_stale(dataset)

    def register_skill(self, dataset: str, zip_bytes: bytes) -> SkillResponse:
        """Upload a skill ZIP, extract to skills/{name}/, register entry."""
        store = self._get_store(dataset)
        skill_data = store.save_skill_zip(zip_bytes)

        service_id = generate_service_id("skill", skill_data.name)
        entry = RegistryEntry(
            service_id=service_id,
            type="skill",
            source="skill_folder",
            skill_data=skill_data,
        )

        with self._lock:
            ds = self._entries.setdefault(dataset, {})
            status = "updated" if service_id in ds else "registered"
            ds[service_id] = entry

        self._regenerate_output(dataset)
        self._mark_taxonomy_stale(dataset)
        return SkillResponse(name=skill_data.name, dataset=dataset,
                             status=status, service_id=service_id)

    def deregister_skill(self, dataset: str, name: str) -> SkillResponse:
        """Remove a skill folder and its registry entry."""
        service_id = generate_service_id("skill", name)

        with self._lock:
            ds = self._entries.get(dataset, {})
            if service_id not in ds:
                return SkillResponse(name=name, dataset=dataset, status="not_found")
            del ds[service_id]

        store = self._get_store(dataset)
        store.remove_skill(name)
        self._regenerate_output(dataset)
        self._mark_taxonomy_stale(dataset)
        return SkillResponse(name=name, dataset=dataset, status="deleted",
                             service_id=service_id)

    def get_skill_zip(self, dataset: str, name: str) -> bytes:
        """Pack a skill folder into a ZIP and return bytes."""
        return self._get_store(dataset).get_skill_zip(name)

    def _do_register(self, dataset: str, entry: RegistryEntry, persistent: bool) -> RegisterResponse:
        """Shared logic for register_generic and register_a2a."""
        with self._lock:
            ds = self._entries.setdefault(dataset, {})
            status = "updated" if entry.service_id in ds else "registered"
            ds[entry.service_id] = entry

        if persistent:
            self._get_store(dataset).save_api_entry(entry)
        self._regenerate_output(dataset)
        self._mark_taxonomy_stale(dataset)
        return RegisterResponse(service_id=entry.service_id, dataset=dataset, status=status)

    # -----------------------------------------------------------------------
    # Deregister
    # -----------------------------------------------------------------------

    def deregister(self, dataset: str, service_id: str) -> DeregisterResponse:
        """Deregister a service."""
        with self._lock:
            ds = self._entries.get(dataset, {})
            if service_id not in ds:
                return DeregisterResponse(service_id=service_id, status="not_found")

            entry = ds[service_id]
            if entry.source == "user_config":
                raise ValueError("Cannot deregister user_config entries via API. Edit user_config.json instead.")
            if entry.source == "skill_folder":
                raise ValueError("Cannot deregister skill entries via generic API. Use DELETE /skills/{name} instead.")

            source = entry.source
            del ds[service_id]

        if source == "api_config":
            self._get_store(dataset).remove_api_entry(service_id)
        self._regenerate_output(dataset)
        self._mark_taxonomy_stale(dataset)
        return DeregisterResponse(service_id=service_id, status="deregistered")

    # -----------------------------------------------------------------------
    # Taxonomy state
    # -----------------------------------------------------------------------

    def get_taxonomy_state(self, dataset: str) -> Optional[TaxonomyState]:
        """Return cached taxonomy state, or None if dataset is not registry-managed."""
        with self._lock:
            return self._taxonomy_states.get(dataset)

    def check_taxonomy_state(self, dataset: str) -> Optional[TaxonomyState]:
        """Return taxonomy state, resolving STALE by re-checking the hash.

        Returns None for datasets not managed by this registry instance.
        """
        with self._lock:
            state = self._taxonomy_states.get(dataset)
        if state is None:
            return None
        if state != TaxonomyState.STALE:
            return state

        # Stale → recompute
        new_state = self._compute_taxonomy_state(dataset)
        with self._lock:
            self._taxonomy_states[dataset] = new_state
        logger.info("Dataset '%s': taxonomy re-checked, state=%s", dataset, new_state.value)
        return new_state

    # -----------------------------------------------------------------------
    # Query (read-only, return snapshots)
    # -----------------------------------------------------------------------

    def list_services(self, dataset: str) -> List[dict]:
        """Return cached output for a dataset."""
        with self._lock:
            return list(self._output_cache.get(dataset, []))

    def list_entries(self, dataset: str) -> List[RegistryEntry]:
        """Return all RegistryEntry objects for a dataset (includes source info)."""
        with self._lock:
            return list(self._entries.get(dataset, {}).values())

    def get_entry(self, dataset: str, service_id: str) -> Optional[RegistryEntry]:
        """Get a single registry entry."""
        with self._lock:
            return self._entries.get(dataset, {}).get(service_id)

    def get_status(self, dataset: Optional[str] = None) -> RegistryStatus:
        """Get registry status summary."""
        with self._lock:
            datasets_to_check = [dataset] if dataset else list(self._entries.keys())
            total = 0
            by_source: Dict[str, int] = {}
            for ds in datasets_to_check:
                for entry in self._entries.get(ds, {}).values():
                    total += 1
                    by_source[entry.source] = by_source.get(entry.source, 0) + 1
            all_datasets = list(self._entries.keys())

        return RegistryStatus(total_services=total, by_source=by_source, datasets=all_datasets)

    def list_datasets(self) -> List[str]:
        """List all datasets that have registry data."""
        with self._lock:
            return list(self._entries.keys())

    def set_on_service_changed(self, callback) -> None:
        """Register a callback(dataset: str) invoked when service.json content changes.

        Called after the file is written, from the same thread that triggered the
        change. The callback should be non-blocking (e.g. schedule background work).
        """
        self._on_service_changed = callback

    # -----------------------------------------------------------------------
    # Internal — output generation
    # -----------------------------------------------------------------------

    def _get_store(self, dataset: str) -> RegistryStore:
        if dataset not in self._stores:
            self._stores[dataset] = RegistryStore(self._database_dir / dataset)
        return self._stores[dataset]

    def _regenerate_output(self, dataset: str) -> bool:
        """Rebuild output cache, write service.json if content changed. Returns True if changed."""
        with self._lock:
            entries = self._entries.get(dataset, {})
            output = [self._entry_to_output(e) for e in entries.values()]
            old_output = self._output_cache.get(dataset)
            changed = output != old_output
            self._output_cache[dataset] = output

        if changed:
            self._get_store(dataset).write_service_json(output)
            if self._on_service_changed:
                self._on_service_changed(dataset)
        return changed

    def _entry_to_output(self, entry: RegistryEntry) -> dict:
        """Convert a RegistryEntry to service.json output format.

        Output schema: {id, type, name, description, metadata}
          - description: system-generated; used by taxonomy build (LLM text input)
          - metadata:    for A2A = full agent card; for generic = {url?, inputSchema?}
        """
        if entry.type == "skill" and entry.skill_data:
            sd = entry.skill_data
            return {
                "id": entry.service_id,
                "type": "skill",
                "name": sd.name,
                "description": sd.description,
                "metadata": {
                    "skill_path": sd.skill_path,
                    "license": sd.license,
                    "files": sd.files,
                },
            }

        if entry.type == "generic" and entry.service_data:
            sd = entry.service_data
            metadata: dict = {}
            if sd.inputSchema:
                metadata["inputSchema"] = sd.inputSchema
            if sd.url:
                metadata["url"] = sd.url
            return {
                "id": entry.service_id,
                "type": "generic",
                "name": sd.name,
                "description": sd.description,
                "metadata": metadata,
            }

        if entry.type == "a2a" and entry.agent_card:
            card = entry.agent_card
            return {
                "id": entry.service_id,
                "type": "a2a",
                "name": card.name,
                "description": build_description(card),   # agent desc + skills (for taxonomy build)
                "metadata": card.model_dump(exclude_none=True),
            }

        # a2a with unresolved card (URL fetch failed)
        return {
            "id": entry.service_id,
            "type": "a2a",
            "name": entry.service_id,
            "description": f"Unresolved agent card: {entry.agent_card_url or 'unknown'}",
            "metadata": {},
        }

    # -----------------------------------------------------------------------
    # Internal — taxonomy state
    # -----------------------------------------------------------------------

    def _init_taxonomy_state(self, dataset: str) -> TaxonomyState:
        """Compute initial taxonomy state on startup and cache it."""
        state = self._compute_taxonomy_state(dataset)
        with self._lock:
            self._taxonomy_states[dataset] = state
        return state

    def _compute_taxonomy_state(self, dataset: str) -> TaxonomyState:
        """Compare current service hash against build_config.json's stored hash."""
        build_config_path = self._database_dir / dataset / "taxonomy" / BUILD_CONFIG_FILE
        taxonomy_path     = self._database_dir / dataset / "taxonomy" / TAXONOMY_FILE

        if not build_config_path.exists() or not taxonomy_path.exists():
            return TaxonomyState.NONEXISTENT

        stored_hash = _read_build_hash(build_config_path)
        if stored_hash is None:
            return TaxonomyState.NONEXISTENT

        with self._lock:
            current_services = self._output_cache.get(dataset, [])
        current_hash = _compute_build_hash(current_services)

        return TaxonomyState.AVAILABLE if current_hash == stored_hash else TaxonomyState.UNAVAILABLE

    def _mark_taxonomy_stale(self, dataset: str):
        """Mark taxonomy STALE after a CRUD operation (only if currently AVAILABLE)."""
        with self._lock:
            if self._taxonomy_states.get(dataset) == TaxonomyState.AVAILABLE:
                self._taxonomy_states[dataset] = TaxonomyState.STALE

    # -----------------------------------------------------------------------
    # Dataset lifecycle
    # -----------------------------------------------------------------------

    def create_dataset(self, name: str, embedding_model: str = "all-MiniLM-L6-v2") -> Path:
        """Create a new empty dataset directory with vector_config.json.

        Returns the dataset directory path.
        Raises ValueError if the dataset already exists.
        """
        from src.vector.utils.embedding import EMBEDDING_MODELS
        ds_dir = self._database_dir / name
        if ds_dir.exists():
            raise ValueError(f"Dataset '{name}' already exists")
        ds_dir.mkdir(parents=True)
        (ds_dir / "query").mkdir()
        # Write vector_config.json
        info = EMBEDDING_MODELS.get(embedding_model, {})
        dim = info.get("dim", 384)
        vc = {"embedding_model": embedding_model, "embedding_dim": dim}
        (ds_dir / "vector_config.json").write_text(
            json.dumps(vc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info("Created dataset '%s' (embedding: %s)", name, embedding_model)
        return ds_dir

    def delete_dataset(self, name: str) -> None:
        """Delete a dataset directory and all internal caches.

        Raises ValueError if the dataset directory does not exist.
        """
        import shutil
        ds_dir = self._database_dir / name
        if not ds_dir.exists():
            raise ValueError(f"Dataset '{name}' does not exist")
        with self._lock:
            self._entries.pop(name, None)
            self._output_cache.pop(name, None)
            self._taxonomy_states.pop(name, None)
            self._stores.pop(name, None)
        shutil.rmtree(ds_dir)
        logger.info("Deleted dataset '%s'", name)

    # -----------------------------------------------------------------------
    # Internal — A2A card fetching
    # -----------------------------------------------------------------------

    def _validate_a2a_card(self, card: AgentCard):
        result = validate_agent_card(card, self._allowed_a2a_versions)
        if not result.valid:
            raise ValueError(f"AgentCard '{card.name}' failed validation: {'; '.join(result.errors)}")
        if result.warnings:
            logger.warning("AgentCard '%s' passed as %s with warnings: %s",
                           card.name, result.matched_version, "; ".join(result.warnings))

    def _fetch_agent_cards_parallel(self, url_entries: List[tuple]):
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(fetch_agent_card, entry.agent_card_url): (dataset, entry)
                for dataset, entry in url_entries
            }
            for future in as_completed(futures):
                dataset, entry = futures[future]
                try:
                    card = future.result()
                    with self._lock:
                        entry.agent_card = card
                    logger.info("Fetched agent card '%s' from %s", entry.service_id, entry.agent_card_url)
                except Exception as e:
                    if entry.agent_card:
                        logger.warning("Failed to fetch %s, using cached snapshot: %s", entry.agent_card_url, e)
                    else:
                        logger.warning("Failed to fetch %s, no cache: %s", entry.agent_card_url, e)

    # -----------------------------------------------------------------------
    # Internal — dataset discovery / global config
    # -----------------------------------------------------------------------

    def _discover_datasets(self) -> List[str]:
        if not self._database_dir.exists():
            return []
        return sorted(
            d.name for d in self._database_dir.iterdir()
            if d.is_dir() and (
                (d / USER_CONFIG_FILE).exists()
                or (d / API_CONFIG_FILE).exists()
                or (d / "skills").is_dir()
            )
        )

    def _distribute_global_config(self):
        try:
            with open(self._global_config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load global config %s: %s", self._global_config_path, e)
            return

        by_dataset: Dict[str, list] = {}
        for svc in data.get("services", []):
            ds = svc.pop("dataset", "default")
            by_dataset.setdefault(ds, []).append(svc)

        for dataset, services in by_dataset.items():
            dataset_dir = self._database_dir / dataset
            dataset_dir.mkdir(parents=True, exist_ok=True)
            user_config_path = dataset_dir / USER_CONFIG_FILE
            if not user_config_path.exists():
                content = json.dumps({"services": services}, ensure_ascii=False, indent=2)
                with open(user_config_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info("Created %s from global config (%d services)", user_config_path, len(services))


# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------

def _compute_build_hash(services: List[dict]) -> str:
    """Hash name+description pairs only, order-independent.

    Only these two fields are used by taxonomy build (LLM classification),
    so only they should trigger a rebuild when changed.
    """
    pairs = sorted((s["name"], s.get("description", "")) for s in services)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False).encode()).hexdigest()


def _read_build_hash(build_config_path: Path) -> Optional[str]:
    """Read service_hash from build_config.json, or None if absent/invalid."""
    try:
        with open(build_config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("service_hash")
    except (json.JSONDecodeError, OSError):
        return None
