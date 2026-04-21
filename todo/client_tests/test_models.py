"""Tests for :mod:`src.client.models` — response dataclasses."""

from __future__ import annotations

import pytest

from src.client.models import (
    AgentDetail,
    DatasetCreateResponse,
    DatasetDeleteResponse,
    DeregisterResponse,
    PatchResponse,
    RegisterResponse,
)


class TestFromDictBasic:
    def test_dataset_create_full_payload(self):
        r = DatasetCreateResponse.from_dict({
            "dataset": "d", "embedding_model": "m",
            "formats": {"a2a": "v0.0"}, "status": "created",
        })
        assert r.dataset == "d"
        assert r.embedding_model == "m"
        assert r.formats == {"a2a": "v0.0"}
        assert r.status == "created"

    def test_dataset_delete(self):
        r = DatasetDeleteResponse.from_dict({"dataset": "d", "status": "deleted"})
        assert r.dataset == "d" and r.status == "deleted"

    def test_register_response(self):
        r = RegisterResponse.from_dict({
            "service_id": "s", "dataset": "d", "status": "registered",
        })
        assert r.service_id == "s"

    def test_patch_response_defaults(self):
        """changed_fields / taxonomy_affected have safe defaults if omitted."""
        r = PatchResponse.from_dict({
            "service_id": "s", "dataset": "d", "status": "updated",
        })
        assert r.changed_fields == []
        assert r.taxonomy_affected is False

    def test_patch_response_full(self):
        r = PatchResponse.from_dict({
            "service_id": "s", "dataset": "d", "status": "updated",
            "changed_fields": ["description", "skills"],
            "taxonomy_affected": True,
        })
        assert r.changed_fields == ["description", "skills"]
        assert r.taxonomy_affected is True

    def test_deregister_response(self):
        r = DeregisterResponse.from_dict({"service_id": "s", "status": "deregistered"})
        assert r.status == "deregistered"

    @pytest.mark.parametrize("status", ["registered", "updated"])
    def test_register_status_enum(self, status):
        r = RegisterResponse.from_dict({"service_id": "s", "dataset": "d", "status": status})
        assert r.status == status

    @pytest.mark.parametrize("status", ["deregistered", "not_found"])
    def test_deregister_status_enum(self, status):
        r = DeregisterResponse.from_dict({"service_id": "s", "status": status})
        assert r.status == status


class TestFromDictForwardCompat:
    def test_tolerates_unknown_fields(self):
        """Extra fields must not break dataclass construction (forward-compat)."""
        r = DatasetCreateResponse.from_dict({
            "dataset": "d", "embedding_model": "m",
            "formats": {}, "status": "created",
            "future_field": "value", "another_extra": 42,
        })
        assert r.dataset == "d"
        assert not hasattr(r, "future_field")
        assert not hasattr(r, "another_extra")

    def test_missing_required_field_raises(self):
        """Missing required field should surface as TypeError from dataclass."""
        with pytest.raises(TypeError):
            DatasetCreateResponse.from_dict({"dataset": "d"})


class TestAgentDetail:
    def test_metadata_preserves_full_card(self):
        data = {
            "id": "s", "type": "a2a", "name": "N", "description": "D",
            "metadata": {
                "protocolVersion": "0.0", "name": "N", "description": "D",
                "agentTeamCount": 3,
                "provider": {"organization": "O"},
            },
        }
        d = AgentDetail.from_dict(data)
        assert d.metadata["agentTeamCount"] == 3
        assert d.metadata["provider"]["organization"] == "O"

    def test_raw_preserves_complete_original(self):
        """raw keeps every key, even ones not declared on the dataclass."""
        data = {
            "id": "s", "type": "a2a", "name": "N", "description": "D",
            "metadata": {"protocolVersion": "0.0"},
            "_backend_internal": "x",
            "unknown_field": [1, 2, 3],
        }
        d = AgentDetail.from_dict(data)
        assert d.raw["_backend_internal"] == "x"
        assert d.raw["unknown_field"] == [1, 2, 3]
        # declared fields still populated
        assert d.id == "s"

    def test_empty_metadata_becomes_empty_dict(self):
        d = AgentDetail.from_dict({"id": "s", "type": "a2a", "name": "N",
                                   "description": "D", "metadata": None})
        assert d.metadata == {}

    def test_missing_fields_default_to_empty(self):
        """AgentDetail.from_dict is tolerant — missing scalars default to ''."""
        d = AgentDetail.from_dict({"id": "s"})
        assert d.id == "s"
        assert d.type == "" and d.name == "" and d.description == ""
        assert d.metadata == {}


class TestMutability:
    """Dataclasses are by default mutable — document the choice explicitly."""

    def test_instances_are_mutable(self):
        r = DatasetDeleteResponse.from_dict({"dataset": "d", "status": "deleted"})
        r.status = "changed"
        assert r.status == "changed"
