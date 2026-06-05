"""Cluster test harness: in-process transport + fake registry.

Two ``ClusterStore`` instances can't share one process via the FastAPI
module-singleton, so component tests wire them directly through an
``InProcessTransport`` that routes a peer ``address`` straight to the
target store's handler methods (the same methods the HTTP router calls).
This exercises the full sync logic without real servers.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pytest

from a2x_registry.cluster.config import ClusterConfig
from a2x_registry.cluster.state import ClusterState
from a2x_registry.cluster.store import ClusterStore
from a2x_registry.cluster.transport import Transport
from a2x_registry.register.models import GenericServiceData, RegistryEntry


# ── fakes ────────────────────────────────────────────────────────────────

class FakeRegistry:
    """Minimal RegistryService surface the ClusterStore depends on."""

    def __init__(self) -> None:
        # dataset -> {sid: (RegistryEntry, wrapped_dict)}
        self._data: Dict[str, Dict[str, Tuple[RegistryEntry, dict]]] = {}
        self._auth_required: set[str] = set()

    def add_generic(self, dataset: str, name: str, description: str = "d",
                    source: str = "api_config") -> str:
        from a2x_registry.register.store import generate_service_id
        sid = generate_service_id("generic", name)
        entry = RegistryEntry(
            service_id=sid, type="generic", source=source,
            service_data=GenericServiceData(name=name, description=description),
        )
        wrapped = {
            "id": sid, "type": "generic", "name": name,
            "description": description, "metadata": {},
        }
        self._data.setdefault(dataset, {})[sid] = (entry, wrapped)
        return sid

    def remove(self, dataset: str, sid: str) -> None:
        self._data.get(dataset, {}).pop(sid, None)

    def set_auth_required(self, dataset: str, required: bool) -> None:
        if required:
            self._auth_required.add(dataset)
        else:
            self._auth_required.discard(dataset)

    # RegistryService-compatible read surface
    def list_datasets(self) -> List[str]:
        return list(self._data)

    def list_entries(self, dataset: str) -> List[RegistryEntry]:
        return [e for e, _ in self._data.get(dataset, {}).values()]

    def list_services(self, dataset: str) -> List[dict]:
        return [w for _, w in self._data.get(dataset, {}).values()]

    def get_entry(self, dataset: str, sid: str) -> Optional[RegistryEntry]:
        rec = self._data.get(dataset, {}).get(sid)
        return rec[0] if rec else None

    def is_auth_required(self, dataset: str) -> bool:
        return dataset in self._auth_required


class InProcessTransport(Transport):
    """Routes peer calls to the target store's handlers in-process."""

    def __init__(self) -> None:
        self._stores: Dict[str, ClusterStore] = {}

    def register(self, address: str, store: ClusterStore) -> None:
        self._stores[address] = store

    def open(self, address: str, body: dict) -> dict:
        return self._stores[address].handle_open(body)

    def digest(self, address, from_node, namespaces):
        return self._stores[address].serve_digest(from_node, namespaces or None)

    def pull(self, address, from_node, keys):
        return self._stores[address].serve_pull(from_node, keys)

    def updates(self, address, from_node, envelopes):
        return self._stores[address].serve_updates(from_node, envelopes)


# ── fixtures ─────────────────────────────────────────────────────────────

def build_store(tmp_path, name, registry, transport, *, auth_store=None) -> ClusterStore:
    state = ClusterState.init(node_id=name, path=tmp_path / f"{name}.json")
    store = ClusterStore(
        state,
        config=ClusterConfig(),
        registry_svc=registry,
        transport=transport,
        advertise=name,  # use the node name as its address in-process
        auth_store_getter=(lambda: auth_store),
    )
    transport.register(name, store)
    return store


@pytest.fixture
def cluster_app(tmp_path, monkeypatch):
    """Boot a real (lite) backend with the cluster module initialized.

    Exercises the HTTP router + store handlers on the *receiver* side; a
    simulated peer drives ``/api/cluster/*`` over the TestClient.
    """
    import importlib.util
    import sys

    heavy = ("numpy", "sentence_transformers", "chromadb", "tqdm")
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda n, *a, **k: None if n in heavy else real_find_spec(n, *a, **k),
    )
    monkeypatch.setenv("A2X_REGISTRY_HOME", str(tmp_path))

    state_file = tmp_path / "cluster_state.json"
    monkeypatch.setenv("A2X_REGISTRY_CLUSTER_STATE", str(state_file))
    ClusterState.init(node_id="B", path=state_file)

    for n in list(sys.modules):
        if n.startswith("a2x_registry"):
            monkeypatch.delitem(sys.modules, n, raising=False)

    from a2x_registry.backend.app import app
    from a2x_registry.backend.startup import run_warmup
    run_warmup()

    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def cluster_pair(tmp_path):
    """Two wired instances A and B with fresh fake registries.

    Returns ``(A, regA, B, regB)``. Addresses equal the node ids
    ("A" / "B"); ``connect_peer("B")`` dials store B in-process.
    """
    transport = InProcessTransport()
    regA, regB = FakeRegistry(), FakeRegistry()
    A = build_store(tmp_path, "A", regA, transport)
    B = build_store(tmp_path, "B", regB, transport)
    return A, regA, B, regB
