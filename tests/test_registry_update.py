"""Integration tests for RegistryService.update_service.

Covers:
  - Partial updates: replace existing fields, add new ones.
  - No format validation (update accepts fields that would fail strict
    validation if they were the whole payload — the contract says updates
    never remove required fields).
  - name / description changes mark taxonomy STALE; other fields do not.
  - user_config-sourced entries are refused.
  - Ephemeral entries keep ``source="ephemeral"`` and persist nothing.
  - API-config entries round-trip to api_config.json.
  - Skill updates rewrite SKILL.md frontmatter and preserve the body.
  - Skill rename moves the folder and updates skill_path/skill metadata.
  - Unknown fields for generic / skill types raise ValueError.
  - Missing service_id raises KeyError.

Run:
    python -m unittest tests.test_registry_update
"""

import json
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.register.models import (
    AgentCard, AgentCapabilities, AgentProvider, AgentSkill,
    RegisterA2ARequest, RegisterGenericRequest, TaxonomyState,
)
from src.register.service import RegistryService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_a2a_card() -> AgentCard:
    return AgentCard(name="Mini", description="A minimal agent.")


def _full_v1_0_card() -> AgentCard:
    return AgentCard(
        name="Full",
        description="Fully spec-compliant agent.",
        version="1.0.0",
        url="https://example.com/a2a",
        protocolVersion="1.0",
        capabilities=AgentCapabilities(),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        provider=AgentProvider(organization="Org", url="https://example.com"),
        skills=[AgentSkill(
            id="s1", name="Skill", description="desc", tags=["t"])],
    )


def _make_skill_zip(name: str, description: str, body: str = "# body\n") -> bytes:
    buf = io.BytesIO()
    content = f"---\nname: {name}\ndescription: {description}\n---\n{body}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", content)
    return buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="a2x_upd_"))
        self.svc = RegistryService(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_dataset(self, name="ds1", formats=None):
        self.svc.create_dataset(name, formats=formats or {
            "generic": "v0.0", "a2a": "v0.0", "skill": "v0.0"})
        return name


# ---------------------------------------------------------------------------
# Generic updates
# ---------------------------------------------------------------------------

class GenericUpdateTests(_Base):
    def setUp(self):
        super().setUp()
        self.ds = self._make_dataset()
        self.svc.register_generic(RegisterGenericRequest(
            dataset=self.ds, name="Calc", description="Basic arithmetic",
            url="https://calc.example.com"))
        entries = self.svc.list_entries(self.ds)
        self.sid = entries[0].service_id

    def test_update_existing_field_replaces(self):
        resp = self.svc.update_service(self.ds, self.sid,
                                       {"description": "Updated desc"})
        self.assertEqual(resp.status, "updated")
        self.assertEqual(resp.changed_fields, ["description"])
        self.assertTrue(resp.taxonomy_affected)
        entry = self.svc.get_entry(self.ds, self.sid)
        self.assertEqual(entry.service_data.description, "Updated desc")
        # Untouched fields survive.
        self.assertEqual(entry.service_data.name, "Calc")
        self.assertEqual(entry.service_data.url, "https://calc.example.com")

    def test_update_adds_missing_input_schema(self):
        resp = self.svc.update_service(self.ds, self.sid,
                                       {"inputSchema": {"type": "object"}})
        self.assertEqual(resp.changed_fields, ["inputSchema"])
        self.assertFalse(resp.taxonomy_affected)
        entry = self.svc.get_entry(self.ds, self.sid)
        self.assertEqual(entry.service_data.inputSchema, {"type": "object"})

    def test_no_op_update_reports_empty_changed_fields(self):
        resp = self.svc.update_service(self.ds, self.sid, {"name": "Calc"})
        self.assertEqual(resp.changed_fields, [])
        self.assertFalse(resp.taxonomy_affected)

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.svc.update_service(self.ds, self.sid, {"bogus": "x"})
        self.assertIn("bogus", str(ctx.exception))

    def test_missing_service_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.svc.update_service(self.ds, "not_a_real_id", {"name": "X"})

    def test_persists_to_api_config(self):
        self.svc.update_service(self.ds, self.sid, {"url": "https://new.example.com"})
        # Fresh service should read the new value from api_config.json.
        fresh = RegistryService(self.tmpdir)
        fresh.startup()
        entry = fresh.get_entry(self.ds, self.sid)
        self.assertEqual(entry.service_data.url, "https://new.example.com")

    def test_ephemeral_not_persisted(self):
        self.svc.register_generic(RegisterGenericRequest(
            dataset=self.ds, name="Temp", description="ephemeral", persistent=False))
        temp_entry = [e for e in self.svc.list_entries(self.ds)
                      if e.service_data and e.service_data.name == "Temp"][0]
        self.svc.update_service(self.ds, temp_entry.service_id,
                                {"description": "edited ephemeral"})
        # Reload → ephemeral service should not have been persisted.
        fresh = RegistryService(self.tmpdir)
        fresh.startup()
        self.assertIsNone(fresh.get_entry(self.ds, temp_entry.service_id))


# ---------------------------------------------------------------------------
# A2A updates
# ---------------------------------------------------------------------------

class A2AUpdateTests(_Base):
    def setUp(self):
        super().setUp()
        # Allow v1.0 so we can register a full card and then verify update
        # doesn't re-run validation.
        self.ds = self._make_dataset(formats={"a2a": "v1.0"})
        self.svc.register_a2a(RegisterA2ARequest(
            dataset=self.ds, agent_card=_full_v1_0_card()))
        self.sid = self.svc.list_entries(self.ds)[0].service_id

    def test_update_description_only(self):
        resp = self.svc.update_service(self.ds, self.sid,
                                       {"description": "New description"})
        self.assertEqual(resp.changed_fields, ["description"])
        self.assertTrue(resp.taxonomy_affected)
        card = self.svc.get_entry(self.ds, self.sid).agent_card
        self.assertEqual(card.description, "New description")
        # Other required v1.0 fields are untouched.
        self.assertEqual(card.version, "1.0.0")
        self.assertEqual(card.url, "https://example.com/a2a")

    def test_update_skips_format_validation(self):
        """Updates do not validate — we can blank the `url` (which v1.0 requires)
        and the call still succeeds. The contract says 'no field reduction' is
        the user's responsibility."""
        resp = self.svc.update_service(self.ds, self.sid, {"url": ""})
        self.assertEqual(resp.changed_fields, ["url"])
        # No validation error raised.
        card = self.svc.get_entry(self.ds, self.sid).agent_card
        self.assertEqual(card.url, "")

    def test_adds_custom_extra_field(self):
        """AgentCard has extra='allow' — update can inject new top-level keys."""
        resp = self.svc.update_service(self.ds, self.sid,
                                       {"customField": "x-value"})
        self.assertIn("customField", resp.changed_fields)
        card_dump = self.svc.get_entry(self.ds, self.sid).agent_card.model_dump()
        self.assertEqual(card_dump.get("customField"), "x-value")

    def test_url_only_change_does_not_mark_stale(self):
        # Force taxonomy state to AVAILABLE so we can verify that changing
        # url alone keeps it there (not flipped to STALE).
        self.svc._taxonomy_states[self.ds] = TaxonomyState.AVAILABLE
        self.svc.update_service(self.ds, self.sid,
                                {"url": "https://new.example.com"})
        self.assertEqual(self.svc._taxonomy_states[self.ds], TaxonomyState.AVAILABLE)

    def test_name_change_marks_stale(self):
        self.svc._taxonomy_states[self.ds] = TaxonomyState.AVAILABLE
        self.svc.update_service(self.ds, self.sid, {"name": "Renamed"})
        self.assertEqual(self.svc._taxonomy_states[self.ds], TaxonomyState.STALE)


# ---------------------------------------------------------------------------
# Skill updates
# ---------------------------------------------------------------------------

class SkillUpdateTests(_Base):
    def setUp(self):
        super().setUp()
        self.ds = self._make_dataset()
        zip_bytes = _make_skill_zip("my-skill", "Original description.",
                                    body="# My Skill\n\nDetailed body.\n")
        self.svc.register_skill(self.ds, zip_bytes)
        self.sid = self.svc.list_entries(self.ds)[0].service_id

    def _read_skill_md(self, name):
        return (self.tmpdir / self.ds / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def test_description_update_rewrites_frontmatter(self):
        self.svc.update_service(self.ds, self.sid, {"description": "New description."})
        content = self._read_skill_md("my-skill")
        self.assertIn("description: New description.", content)
        self.assertIn("name: my-skill", content)
        # Body preserved.
        self.assertIn("# My Skill", content)
        self.assertIn("Detailed body.", content)

    def test_rename_moves_folder_and_updates_md(self):
        resp = self.svc.update_service(self.ds, self.sid,
                                       {"name": "renamed-skill"})
        self.assertIn("name", resp.changed_fields)
        # Old folder gone, new folder present.
        self.assertFalse((self.tmpdir / self.ds / "skills" / "my-skill").exists())
        self.assertTrue((self.tmpdir / self.ds / "skills" / "renamed-skill" / "SKILL.md").exists())
        # Entry reflects new path but retains original service_id.
        entry = self.svc.get_entry(self.ds, self.sid)
        self.assertEqual(entry.skill_data.name, "renamed-skill")
        self.assertEqual(entry.skill_data.skill_path, "skills/renamed-skill")
        # Frontmatter carries the new name.
        self.assertIn("name: renamed-skill", self._read_skill_md("renamed-skill"))

    def test_rename_to_existing_folder_rejected(self):
        other = _make_skill_zip("other", "Another skill.")
        self.svc.register_skill(self.ds, other)
        with self.assertRaises(ValueError) as ctx:
            self.svc.update_service(self.ds, self.sid, {"name": "other"})
        self.assertIn("already exists", str(ctx.exception))
        # Original folder still in place.
        self.assertTrue((self.tmpdir / self.ds / "skills" / "my-skill").exists())

    def test_unknown_skill_field_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.svc.update_service(self.ds, self.sid, {"skill_path": "hack"})
        self.assertIn("skill_path", str(ctx.exception))

    def test_license_added_when_absent(self):
        """license was not in the original SKILL.md; update should append it."""
        resp = self.svc.update_service(self.ds, self.sid,
                                       {"license": "MIT"})
        self.assertIn("license", resp.changed_fields)
        self.assertFalse(resp.taxonomy_affected)
        content = self._read_skill_md("my-skill")
        self.assertIn("license: MIT", content)


# ---------------------------------------------------------------------------
# Source-based gating
# ---------------------------------------------------------------------------

class SourceGatingTests(_Base):
    def test_user_config_update_refused(self):
        ds_dir = self.tmpdir / "ds1"
        ds_dir.mkdir()
        (ds_dir / "register_config.json").write_text(
            json.dumps({"formats": {"generic": "v0.0"}}), encoding="utf-8")
        (ds_dir / "user_config.json").write_text(json.dumps({"services": [
            {"type": "generic", "name": "FromFile", "description": "user config entry"},
        ]}), encoding="utf-8")

        self.svc.startup()
        entries = self.svc.list_entries("ds1")
        self.assertEqual(len(entries), 1)
        sid = entries[0].service_id

        with self.assertRaises(ValueError) as ctx:
            self.svc.update_service("ds1", sid, {"description": "tampered"})
        self.assertIn("user_config", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
