"""Registration module for A2X Registry."""

from .service import RegistryService
from .store import RegistryStore
from .validation import (
    DEFAULT_FORMAT_CONFIG,
    SUPPORTED_SERVICE_TYPES,
    FormatValidator,
    ValidationResult,
    normalize_format_config,
    validate_agent_card,
    validate_service,
)

__all__ = [
    "RegistryService",
    "RegistryStore",
    "FormatValidator",
    "ValidationResult",
    "DEFAULT_FORMAT_CONFIG",
    "SUPPORTED_SERVICE_TYPES",
    "normalize_format_config",
    "validate_agent_card",
    "validate_service",
]
