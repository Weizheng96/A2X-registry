"""ClusterConfig.from_env — deploy-time override of liveness knobs."""

from __future__ import annotations

from a2x_registry.cluster.config import ClusterConfig


def test_defaults_when_no_env(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("A2X_REGISTRY_CLUSTER_"):
            monkeypatch.delenv(k, raising=False)
    cfg = ClusterConfig.from_env()
    assert cfg.beacon_ttl == 30 and cfg.beacon_grace == 15
    assert cfg.tombstone_retention == 45.0


def test_override_beacon_ttl_and_grace(monkeypatch):
    monkeypatch.setenv("A2X_REGISTRY_CLUSTER_BEACON_TTL", "10")
    monkeypatch.setenv("A2X_REGISTRY_CLUSTER_BEACON_GRACE", "5")
    monkeypatch.setenv("A2X_REGISTRY_CLUSTER_ANTI_ENTROPY_INTERVAL", "7.5")
    cfg = ClusterConfig.from_env()
    assert cfg.beacon_ttl == 10
    assert cfg.beacon_grace == 5
    assert cfg.tombstone_retention == 15.0       # derived, follows the overrides
    assert cfg.anti_entropy_interval == 7.5
    # Untouched knobs keep defaults.
    assert cfg.hold_timeout == 30.0


def test_int_field_tolerates_float_string(monkeypatch):
    monkeypatch.setenv("A2X_REGISTRY_CLUSTER_BEACON_TTL", "20.0")
    assert ClusterConfig.from_env().beacon_ttl == 20


def test_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("A2X_REGISTRY_CLUSTER_BEACON_TTL", "abc")
    cfg = ClusterConfig.from_env()
    assert cfg.beacon_ttl == 30                   # default, not a crash
