"""Tests for :mod:`src.client._internal` — pure helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.client import _internal as _i


class TestBodyBuilders:
    def test_create_dataset_default_formats(self):
        body = _i.build_create_dataset_body("ds", "m", _i.UNSET)
        assert body == {"name": "ds", "embedding_model": "m", "formats": {"a2a": "v0.0"}}

    def test_create_dataset_explicit_none_omits(self):
        body = _i.build_create_dataset_body("ds", "m", None)
        assert "formats" not in body

    def test_create_dataset_explicit_dict(self):
        body = _i.build_create_dataset_body("ds", "m", {"generic": "v0.0"})
        assert body["formats"] == {"generic": "v0.0"}

    def test_register_agent_body_omits_service_id_when_none(self):
        body = _i.build_register_agent_body({"name": "n"}, None, True)
        assert "service_id" not in body
        assert body["persistent"] is True

    def test_register_agent_body_preserves_agent_card(self):
        card = {"protocolVersion": "0.0", "name": "n", "description": "d"}
        body = _i.build_register_agent_body(card, "sid", False)
        assert body["agent_card"] is card  # passed through unchanged
        assert body["service_id"] == "sid"
        assert body["persistent"] is False

    @pytest.mark.parametrize("count", [0, 1, 100])
    def test_team_count_accepts_non_negative_int(self, count):
        assert _i.build_team_count_body(count) == {"agentTeamCount": count}

    @pytest.mark.parametrize("bad", [-1, True, False, 1.5, "3", None, []])
    def test_team_count_rejects_non_int(self, bad):
        with pytest.raises(ValueError):
            _i.build_team_count_body(bad)


class TestUrlHelpers:
    def test_paths_have_no_leading_slash(self):
        # L3: leading slash would replace the base_url path under subpath mounts.
        assert not _i.dataset_path("ds").startswith("/")
        assert not _i.services_path("ds").startswith("/")
        assert not _i.service_path("ds", "s").startswith("/")
        assert not _i.a2a_register_path("ds").startswith("/")
        assert not _i.DATASETS_ROOT.startswith("/")

    def test_encoding_of_special_chars(self):
        # URL-encoded segments survive transmission intact.
        assert _i.dataset_path("a/b") == "api/datasets/a%2Fb"
        assert _i.service_path("ds x", "s&t") == "api/datasets/ds%20x/services/s%26t"

    def test_normalize_base_url_adds_trailing_slash(self):
        assert _i.normalize_base_url("http://h") == "http://h/"
        assert _i.normalize_base_url("http://h/") == "http://h/"
        assert _i.normalize_base_url("http://h/prefix") == "http://h/prefix/"
        assert _i.normalize_base_url("http://h/prefix/") == "http://h/prefix/"


class TestOwnershipResolution:
    def test_none_returns_default(self):
        assert _i.resolve_ownership_file(None) == _i.DEFAULT_OWNERSHIP_FILE

    def test_false_returns_none(self):
        assert _i.resolve_ownership_file(False) is None

    def test_path_passthrough(self, tmp_path: Path):
        p = tmp_path / "x.json"
        assert _i.resolve_ownership_file(p) == p

    def test_str_coerced(self):
        assert _i.resolve_ownership_file("/tmp/x.json") == Path("/tmp/x.json")

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            _i.resolve_ownership_file(123)  # type: ignore[arg-type]


class TestHeaderBuilder:
    def test_no_api_key_returns_none(self):
        assert _i.build_default_headers(None) is None
        assert _i.build_default_headers("") is None

    def test_api_key_sets_bearer(self):
        h = _i.build_default_headers("secret")
        assert h == {"Authorization": "Bearer secret"}
