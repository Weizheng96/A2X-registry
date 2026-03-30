"""A2A Agent Card format validation.

Validates AgentCard data against allowed protocol versions.
Each version defines its own set of required/optional field checks.

Supported versions:
  - "v0.0": Minimal — only name and description required
  - "v1.0": Full A2A v1.0 spec compliance

Validation order: strictest first (v1.0 → v0.0). A card that passes v1.0
is reported as v1.0, even if v0.0 is also allowed.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set

from .models import AgentCard, AgentCapabilities, AgentProvider

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_VERSIONS = {"v0.0", "v1.0"}

# Strictest first — a card that passes v1.0 should be reported as v1.0
_VERSION_ORDER = ["v1.0", "v0.0"]


@dataclass
class ValidationResult:
    """Result of an AgentCard validation."""
    valid: bool
    matched_version: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_agent_card(card: AgentCard, allowed_versions: Optional[Set[str]] = None) -> ValidationResult:
    """Validate an AgentCard against allowed protocol versions.

    Tries each allowed version from strictest to loosest. Returns the
    strictest version the card satisfies.
    """
    if allowed_versions is None:
        allowed_versions = DEFAULT_ALLOWED_VERSIONS

    versions_to_try = [v for v in _VERSION_ORDER if v in allowed_versions]
    if not versions_to_try:
        return ValidationResult(valid=False, errors=[f"No known versions in allowed set: {allowed_versions}"])

    all_errors = {}
    for version in versions_to_try:
        result = _validate_for_version(card, version)
        if result.valid:
            return result
        all_errors[version] = result.errors

    # None passed — report errors from the loosest version (most likely to help user fix)
    loosest = versions_to_try[-1]
    return ValidationResult(
        valid=False,
        errors=[f"AgentCard does not satisfy any allowed version {allowed_versions}. "
                f"Errors for {loosest}: {'; '.join(all_errors[loosest])}"],
    )


def _validate_for_version(card: AgentCard, version: str) -> ValidationResult:
    if version == "v0.0":
        return _validate_v0_0(card)
    elif version == "v1.0":
        return _validate_v1_0(card)
    return ValidationResult(valid=False, errors=[f"Unknown version: {version}"])


# ---------------------------------------------------------------------------
# v0.0 — Minimal: only name + description
# ---------------------------------------------------------------------------

def _validate_v0_0(card: AgentCard) -> ValidationResult:
    errors = []
    warnings = []

    if not _has_text(card.name):
        errors.append("name is required")
    if not _has_text(card.description):
        errors.append("description is required")

    if not card.version:
        warnings.append("version is recommended")
    if not card.skills:
        warnings.append("skills is recommended (agent has no declared capabilities)")

    return ValidationResult(valid=not errors, matched_version="v0.0" if not errors else None,
                            errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# v1.0 — Full A2A v1.0 spec (from a2a.proto)
# ---------------------------------------------------------------------------

def _validate_v1_0(card: AgentCard) -> ValidationResult:
    errors = []
    warnings = []

    # Top-level REQUIRED
    if not _has_text(card.name):
        errors.append("name is required")
    if not _has_text(card.description):
        errors.append("description is required")
    if not _has_text(card.version):
        errors.append("version is required")
    if not _has_text(card.url):
        errors.append("url (or supported_interfaces) is required")

    # capabilities: must be present (any truthy type — dict, list, AgentCapabilities)
    if card.capabilities is None:
        errors.append("capabilities is required (can be empty object)")

    if not card.defaultInputModes:
        errors.append('defaultInputModes is required (e.g. ["text/plain"])')
    if not card.defaultOutputModes:
        errors.append('defaultOutputModes is required (e.g. ["text/plain"])')

    # Skills
    if not card.skills:
        errors.append("skills is required (at least one skill)")
    else:
        for i, skill in enumerate(card.skills):
            p = f"skills[{i}]"
            if not _has_text(skill.id):
                errors.append(f"{p}.id is required")
            if not _has_text(skill.name):
                errors.append(f"{p}.name is required")
            if not _has_text(skill.description):
                errors.append(f"{p}.description is required")
            if not skill.tags:
                errors.append(f"{p}.tags is required (at least one tag)")

    # Provider (if present and structured, validate required sub-fields)
    if isinstance(card.provider, AgentProvider):
        if not _has_text(card.provider.organization):
            errors.append("provider.organization is required when provider is present")
        if not _has_text(card.provider.url):
            errors.append("provider.url is required when provider is present")

    # Warnings
    if not _has_text(card.protocolVersion):
        warnings.append('protocolVersion is recommended (e.g. "1.0")')
    if card.provider is None:
        warnings.append("provider is recommended")
    if not _has_text(card.documentationUrl):
        warnings.append("documentationUrl is recommended")

    return ValidationResult(valid=not errors, matched_version="v1.0" if not errors else None,
                            errors=errors, warnings=warnings)


def _has_text(value) -> bool:
    """Check if a value is a non-empty, non-whitespace string."""
    return bool(value and isinstance(value, str) and value.strip())
