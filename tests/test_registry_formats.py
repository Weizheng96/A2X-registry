"""Integration tests for dataset-level registration format gating.

Covers:
  - create_dataset persists register_config.json with requested formats (or defaults).
  - get_register_config returns persisted config; set_register_config roundtrips.
  - register_generic / register_a2a / register_skill raise when type not in allow-list.
  - register_generic / register_a2a accept v0.0 payload with only name+description.
  - Dataset-level min_version=v1.0 rejects a minimal A2A card, accepts a full one.
  - Startup drops user_config entries whose type is not in the dataset's config.
  - Skill ZIP upload honours the skill type gate.
  - Missing register_config.json → defaults (three types at v0.0).

Run:
    python -m unittest tests.test_registry_formats
"""

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.register.models import (
    AgentCard, AgentCapabilities, AgentProvider, AgentSkill,
    RegisterA2ARequest, RegisterGenericRequest,
)
from src.register.service import RegistryService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _full_v1_0_card(**overrides) -> AgentCard:
    base = dict(
        name="A2ACard",
        description="Does a thing.",
        version="1.0.0",
        url="https://example.com/a2a",
        protocolVersion="1.0",
        capabilities=AgentCapabilities(),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        documentationUrl="https://example.com/docs",
        provider=AgentProvider(organization="Org", url="https://example.com"),
        skills=[AgentSkill(
            id="s1", name="Skill", description="desc", tags=["t"], examples=["ex"])],
    )
    base.update(overrides)
    return AgentCard(**base)


def _minimal_a2a_card() -> AgentCard:
    return AgentCard(name="Mini", description="A minimal agent.")


def _make_skill_zip(name: str, description: str) -> bytes:
    """Build an in-memory skill ZIP with valid SKILL.md frontmatter."""
    buf = io.BytesIO()
    content = f"---\nname: {name}\ndescription: {description}\n---\n# body\n"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", content)
    return buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="a2x_reg_"))
        self.svc = RegistryService(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# create_dataset / register_config persistence
# ---------------------------------------------------------------------------

class CreateDatasetTests(_Base):
    def test_default_formats_written(self):
        self.svc.create_dataset("ds1")
        cfg_path = self.tmpdir / "ds1" / "register_config.json"
        self.assertTrue(cfg_path.exists())
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["formats"], {"generic": "v0.0", "a2a": "v0.0", "skill": "v0.0"})

    def test_custom_formats_written(self):
        self.svc.create_dataset("ds1", formats={"generic": "v0.0", "a2a": "v1.0"})
        cfg = self.svc.get_register_config("ds1")
        self.assertEqual(cfg, {"generic": "v0.0", "a2a": "v1.0"})
        # Skill was not declared — it is now disallowed.
        self.assertNotIn("skill", cfg)

    def test_empty_formats_rejected_with_fail_fast(self):
        with self.assertRaises(ValueError):
            self.svc.create_dataset("ds1", formats={"bogus": "v0.0"})
        # Fail-fast: no directory was created.
        self.assertFalse((self.tmpdir / "ds1").exists())

    def test_set_register_config_roundtrip(self):
        self.svc.create_dataset("ds1")
        new_cfg = self.svc.set_register_config("ds1", {"a2a": "v1.0"})
        self.assertEqual(new_cfg, {"a2a": "v1.0"})
        self.assertEqual(self.svc.get_register_config("ds1"), {"a2a": "v1.0"})

    def test_set_register_config_rejects_empty(self):
        self.svc.create_dataset("ds1")
        with self.assertRaises(ValueError):
            self.svc.set_register_config("ds1", {"unknown": "v0.0"})

    def test_missing_file_returns_defaults(self):
        """A dataset discovered without register_config.json falls back to defaults."""
        (self.tmpdir / "legacy").mkdir()
        (self.tmpdir / "legacy" / "user_config.json").write_text(
            json.dumps({"services": []}), encoding="utf-8")
        self.svc.startup()
        cfg = self.svc.get_register_config("legacy")
        self.assertEqual(cfg, {"generic": "v0.0", "a2a": "v0.0", "skill": "v0.0"})


# ---------------------------------------------------------------------------
# Registration gated by formats
# ---------------------------------------------------------------------------

class RegisterGenericTests(_Base):
    def setUp(self):
        super().setUp()
        self.svc.create_dataset("ds1", formats={"generic": "v0.0"})

    def test_v0_0_accepts_name_and_description(self):
        req = RegisterGenericRequest(dataset="ds1", name="Foo", description="Bar")
        resp = self.svc.register_generic(req)
        self.assertEqual(resp.status, "registered")

    def test_missing_name_rejected(self):
        req = RegisterGenericRequest(dataset="ds1", name="", description="Bar")
        with self.assertRaises(ValueError) as ctx:
            self.svc.register_generic(req)
        self.assertIn("name", str(ctx.exception))

    def test_type_not_in_allowed_list_rejected(self):
        self.svc.set_register_config("ds1", {"a2a": "v0.0"})  # disallow generic
        req = RegisterGenericRequest(dataset="ds1", name="Foo", description="Bar")
        with self.assertRaises(ValueError) as ctx:
            self.svc.register_generic(req)
        self.assertIn("generic", str(ctx.exception))
        self.assertIn("not allowed", str(ctx.exception))


class RegisterA2ATests(_Base):
    def test_v0_0_accepts_minimal_card(self):
        self.svc.create_dataset("ds1", formats={"a2a": "v0.0"})
        req = RegisterA2ARequest(dataset="ds1", agent_card=_minimal_a2a_card())
        resp = self.svc.register_a2a(req)
        self.assertEqual(resp.status, "registered")

    def test_min_version_v1_0_rejects_minimal_card(self):
        self.svc.create_dataset("ds1", formats={"a2a": "v1.0"})
        req = RegisterA2ARequest(dataset="ds1", agent_card=_minimal_a2a_card())
        with self.assertRaises(ValueError):
            self.svc.register_a2a(req)

    def test_min_version_v1_0_accepts_full_card(self):
        self.svc.create_dataset("ds1", formats={"a2a": "v1.0"})
        req = RegisterA2ARequest(dataset="ds1", agent_card=_full_v1_0_card())
        resp = self.svc.register_a2a(req)
        self.assertEqual(resp.status, "registered")

    def test_a2a_disallowed_when_type_not_declared(self):
        self.svc.create_dataset("ds1", formats={"generic": "v0.0"})  # no a2a
        req = RegisterA2ARequest(dataset="ds1", agent_card=_minimal_a2a_card())
        with self.assertRaises(ValueError) as ctx:
            self.svc.register_a2a(req)
        self.assertIn("a2a", str(ctx.exception))


class RegisterSkillTests(_Base):
    def test_skill_zip_accepted_when_allowed(self):
        self.svc.create_dataset("ds1", formats={"skill": "v0.0"})
        zip_bytes = _make_skill_zip("my-skill", "A skill for testing.")
        resp = self.svc.register_skill("ds1", zip_bytes)
        self.assertEqual(resp.status, "registered")
        self.assertEqual(resp.name, "my-skill")

    def test_skill_rejected_when_type_not_allowed(self):
        self.svc.create_dataset("ds1", formats={"generic": "v0.0"})  # no skill
        zip_bytes = _make_skill_zip("my-skill", "A skill for testing.")
        with self.assertRaises(ValueError) as ctx:
            self.svc.register_skill("ds1", zip_bytes)
        self.assertIn("skill", str(ctx.exception))
        # Fail-fast: the ZIP must not have been extracted.
        self.assertFalse((self.tmpdir / "ds1" / "skills" / "my-skill").exists())


# ---------------------------------------------------------------------------
# Startup filtering
# ---------------------------------------------------------------------------

class StartupFilteringTests(_Base):
    def test_startup_drops_disallowed_type_from_user_config(self):
        """user_config with an a2a entry is dropped when dataset excludes a2a."""
        ds_dir = self.tmpdir / "ds1"
        ds_dir.mkdir()
        # register_config allows only generic.
        (ds_dir / "register_config.json").write_text(
            json.dumps({"formats": {"generic": "v0.0"}}), encoding="utf-8")
        # user_config has one generic and one a2a — only generic should survive.
        (ds_dir / "user_config.json").write_text(json.dumps({"services": [
            {"type": "generic", "name": "Keep", "description": "should be kept"},
            {"type": "a2a", "agent_card": {
                "name": "Drop", "description": "should be dropped"}},
        ]}), encoding="utf-8")

        self.svc.startup()
        entries = self.svc.list_entries("ds1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].type, "generic")
        self.assertEqual(entries[0].service_data.name, "Keep")

    def test_startup_drops_minimal_a2a_when_min_version_v1_0(self):
        ds_dir = self.tmpdir / "ds2"
        ds_dir.mkdir()
        (ds_dir / "register_config.json").write_text(
            json.dumps({"formats": {"a2a": "v1.0"}}), encoding="utf-8")
        (ds_dir / "user_config.json").write_text(json.dumps({"services": [
            {"type": "a2a", "agent_card": {"name": "Mini", "description": "minimal"}},
        ]}), encoding="utf-8")

        self.svc.startup()
        self.assertEqual(self.svc.list_entries("ds2"), [])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
