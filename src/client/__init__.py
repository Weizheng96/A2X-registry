"""A2X Registry client SDK.

Public entry points:

- :class:`A2XClient` — synchronous client
- :class:`AsyncA2XClient` — asynchronous client (mirrors ``A2XClient``)

Response dataclasses and the exception hierarchy are re-exported for
``except``/``isinstance`` use.
"""

from .async_client import AsyncA2XClient
from .client import A2XClient
from .errors import (
    A2XConnectionError,
    A2XError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ServerError,
    UnexpectedServiceTypeError,
    UserConfigDeregisterForbiddenError,  # deprecated alias
    UserConfigServiceImmutableError,
    ValidationError,
)
from .models import (
    AgentBrief,
    AgentDetail,
    BlankAgentInfo,
    DatasetCreateResponse,
    DatasetDeleteResponse,
    DeregisterResponse,
    PatchResponse,
    RegisterResponse,
)

__all__ = [
    "A2XClient",
    "AsyncA2XClient",
    # Errors
    "A2XError",
    "A2XConnectionError",
    "A2XHTTPError",
    "NotFoundError",
    "ValidationError",
    "UserConfigServiceImmutableError",
    "UserConfigDeregisterForbiddenError",
    "UnexpectedServiceTypeError",
    "ServerError",
    "NotOwnedError",
    # Models
    "DatasetCreateResponse",
    "DatasetDeleteResponse",
    "RegisterResponse",
    "PatchResponse",
    "DeregisterResponse",
    "AgentBrief",
    "AgentDetail",
    "BlankAgentInfo",
]

__version__ = "0.1.0"
