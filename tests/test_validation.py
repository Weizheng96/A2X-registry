"""Unit tests for src/register/validation.py.

Covers:
  - FormatValidator base-class version iteration (old → new, first pass wins).
  - Each concrete validator's v0.0 check (name + description only).
  - A2AValidator v1.0 full-spec field check.
  - validate_service dispatch, unknown types.
  - normalize_format_config (drops unknown types / versions, accepts two forms).
  - validate_agent_card legacy shim.

Run:
    python -m unittest tests.test_validation
"""

import unittest

from src.register.models import AgentCard, AgentCapabilities, AgentProvider, AgentSkill
from src.register.validation import (
    A2AValidator,
    DEFAULT_FORMAT_CONFIG,
    FormatValidator,
    GenericValidator,
    SkillValidator,
    SUPPORTED_SERVICE_TYPES,
    ValidationResult,
    normalize_format_config,
    validate_agent_card,
    validate_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_v1_0_card(**overrides) -> AgentCard:
    base = dict(
        name="CardName",
        description="Card description.",
        version="1.0.0",
        url="https://example.com/a2a",
        protocolVersion="1.0",
        capabilities=AgentCapabilities(),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        documentationUrl="https://example.com/docs",
        provider=AgentProvider(organization="Example Inc", url="https://example.com"),
        skills=[AgentSkill(
            id="s1", name="Skill One", description="desc",
            tags=["tag1"], examples=["ex"],
        )],
    )
    base.update(overrides)
    return AgentCard(**base)


# ---------------------------------------------------------------------------
# Generic / Skill validators
# ---------------------------------------------------------------------------

class GenericValidatorTests(unittest.TestCase):
    def setUp(self):
        self.v = GenericValidator()

    def test_v0_0_passes_with_name_and_description(self):
        r = self.v.validate({"name": "Foo", "description": "Bar"})
        self.assertTrue(r.valid, r.errors)
        self.assertEqual(r.matched_version, "v0.0")
        self.assertEqual(r.service_type, "generic")

    def test_missing_name_fails(self):
        r = self.v.validate({"description": "Bar"})
        self.assertFalse(r.valid)
        self.assertTrue(any("name" in e for e in r.errors))

    def test_missing_description_fails(self):
        r = self.v.validate({"name": "Foo"})
        self.assertFalse(r.valid)
        self.assertTrue(any("description" in e for e in r.errors))

    def test_whitespace_name_rejected(self):
        r = self.v.validate({"name": "   ", "description": "Bar"})
        self.assertFalse(r.valid)

    def test_min_version_above_supported_fails(self):
        r = self.v.validate({"name": "Foo", "description": "Bar"}, min_version="v9.9")
        self.assertFalse(r.valid)
        self.assertIn("No generic version", r.errors[0])


class SkillValidatorTests(unittest.TestCase):
    def test_v0_0_passes(self):
        r = SkillValidator().validate({"name": "algo-art", "description": "Create art"})
        self.assertTrue(r.valid)
        self.assertEqual(r.matched_version, "v0.0")


# ---------------------------------------------------------------------------
# A2A validator
# ---------------------------------------------------------------------------

class A2AValidatorTests(unittest.TestCase):
    def setUp(self):
        self.v = A2AValidator()

    def test_v0_0_passes_with_minimal_card(self):
        card = AgentCard(name="Agent", description="Does things")
        r = self.v.validate(card)
        self.assertTrue(r.valid, r.errors)
        # v0.0 is the oldest; with default min_version="v0.0" it wins first.
        self.assertEqual(r.matched_version, "v0.0")
        self.assertTrue(any("version is recommended" in w for w in r.warnings))

    def test_v0_0_min_version_rejects_name_only(self):
        r = self.v.validate({"description": "no name"}, min_version="v0.0")
        self.assertFalse(r.valid)

    def test_oldest_first_iteration(self):
        """A v1.0-compliant card is reported as v0.0 (the oldest version that passes)."""
        card = _full_v1_0_card()
        r = self.v.validate(card)
        self.assertTrue(r.valid)
        self.assertEqual(r.matched_version, "v0.0")

    def test_min_version_v1_0_skips_v0_0(self):
        card = _full_v1_0_card()
        r = self.v.validate(card, min_version="v1.0")
        self.assertTrue(r.valid, r.errors)
        self.assertEqual(r.matched_version, "v1.0")

    def test_v1_0_rejects_minimal_card(self):
        card = AgentCard(name="Agent", description="Does things")
        r = self.v.validate(card, min_version="v1.0")
        self.assertFalse(r.valid)
        # The error message carries the newest version's missing fields.
        joined = " ".join(r.errors)
        self.assertIn("version is required", joined)
        self.assertIn("url", joined)
        self.assertIn("skills is required", joined)

    def test_v1_0_skill_requires_all_subfields(self):
        card = _full_v1_0_card(skills=[AgentSkill(id="", name="x", description="y", tags=[])])
        r = self.v.validate(card, min_version="v1.0")
        self.assertFalse(r.valid)
        joined = " ".join(r.errors)
        self.assertIn("skills[0].id", joined)
        self.assertIn("skills[0].tags", joined)

    def test_v1_0_provider_missing_org_rejected(self):
        card = _full_v1_0_card(provider=AgentProvider(organization="", url="https://x"))
        r = self.v.validate(card, min_version="v1.0")
        self.assertFalse(r.valid)
        self.assertTrue(any("provider.organization" in e for e in r.errors))


# ---------------------------------------------------------------------------
# Module-level facade
# ---------------------------------------------------------------------------

class ValidateServiceDispatchTests(unittest.TestCase):
    def test_dispatch_generic(self):
        r = validate_service("generic", {"name": "X", "description": "Y"})
        self.assertTrue(r.valid)
        self.assertEqual(r.service_type, "generic")

    def test_unknown_type_fails(self):
        r = validate_service("unknown", {"name": "X", "description": "Y"})
        self.assertFalse(r.valid)
        self.assertIn("Unknown service type", r.errors[0])

    def test_supported_types_match_validators(self):
        self.assertEqual(set(SUPPORTED_SERVICE_TYPES), {"generic", "a2a", "skill"})
        self.assertEqual(set(DEFAULT_FORMAT_CONFIG.keys()), set(SUPPORTED_SERVICE_TYPES))
        self.assertTrue(all(v == "v0.0" for v in DEFAULT_FORMAT_CONFIG.values()))


# ---------------------------------------------------------------------------
# normalize_format_config
# ---------------------------------------------------------------------------

class NormalizeFormatConfigTests(unittest.TestCase):
    def test_flat_strings_accepted(self):
        out = normalize_format_config({"generic": "v0.0", "a2a": "v1.0"})
        self.assertEqual(out, {"generic": "v0.0", "a2a": "v1.0"})

    def test_nested_min_version_accepted(self):
        out = normalize_format_config({"a2a": {"min_version": "v1.0"}})
        self.assertEqual(out, {"a2a": "v1.0"})

    def test_drops_unknown_type(self):
        out = normalize_format_config({"generic": "v0.0", "bogus": "v0.0"})
        self.assertEqual(out, {"generic": "v0.0"})

    def test_drops_unknown_version(self):
        out = normalize_format_config({"generic": "v9.9"})
        self.assertEqual(out, {})

    def test_drops_non_string(self):
        out = normalize_format_config({"generic": 42})
        self.assertEqual(out, {})

    def test_none_input(self):
        self.assertEqual(normalize_format_config(None), {})


# ---------------------------------------------------------------------------
# Legacy validate_agent_card shim
# ---------------------------------------------------------------------------

class LegacyValidateAgentCardTests(unittest.TestCase):
    def test_default_allowed_versions_pass_minimal(self):
        card = AgentCard(name="Agent", description="Does things")
        r = validate_agent_card(card)
        self.assertTrue(r.valid)
        # Oldest-first iteration means v0.0 wins.
        self.assertEqual(r.matched_version, "v0.0")

    def test_restricted_to_v1_0_rejects_minimal(self):
        card = AgentCard(name="Agent", description="Does things")
        r = validate_agent_card(card, allowed_versions={"v1.0"})
        self.assertFalse(r.valid)

    def test_empty_allowed_set_fails_gracefully(self):
        card = AgentCard(name="Agent", description="Does things")
        r = validate_agent_card(card, allowed_versions=set())
        self.assertFalse(r.valid)
        self.assertIn("No known A2A versions", r.errors[0])


if __name__ == "__main__":
    unittest.main()
