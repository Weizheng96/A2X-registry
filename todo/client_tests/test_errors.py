"""Tests for :mod:`src.client.errors` — exception hierarchy."""

from __future__ import annotations

import pytest

from src.client.errors import (
    A2XConnectionError,
    A2XError,
    A2XHTTPError,
    NotFoundError,
    NotOwnedError,
    ServerError,
    UnexpectedServiceTypeError,
    UserConfigDeregisterForbiddenError,  # backward-compat alias
    UserConfigServiceImmutableError,
    ValidationError,
)


class TestHierarchy:
    @pytest.mark.parametrize("cls, parent", [
        (A2XConnectionError, A2XError),
        (A2XHTTPError, A2XError),
        (NotFoundError, A2XHTTPError),
        (ValidationError, A2XHTTPError),
        (UserConfigServiceImmutableError, ValidationError),
        (UnexpectedServiceTypeError, A2XHTTPError),
        (ServerError, A2XHTTPError),
        (NotOwnedError, A2XError),
    ])
    def test_subclass_relations(self, cls, parent):
        assert issubclass(cls, parent)

    def test_not_owned_is_not_http_error(self):
        """Local ownership failures carry no HTTP context."""
        assert not issubclass(NotOwnedError, A2XHTTPError)

    def test_deprecated_alias_is_identical(self):
        """Old name kept as alias for the renamed class (L1)."""
        assert UserConfigDeregisterForbiddenError is UserConfigServiceImmutableError


class TestConstruction:
    def test_base_error_carries_status_and_payload(self):
        e = NotFoundError("hit 404", status_code=404, payload={"detail": "x"})
        assert e.status_code == 404
        assert e.payload == {"detail": "x"}
        assert "hit 404" in str(e)

    def test_base_error_optional_fields(self):
        e = ServerError("oops")
        assert e.status_code is None
        assert e.payload is None

    def test_not_owned_has_context_attrs(self):
        e = NotOwnedError("ds1", "sid_abc")
        assert e.dataset == "ds1"
        assert e.service_id == "sid_abc"
        assert e.status_code is None
        assert e.payload is None
        # Message helps log diagnosis
        msg = str(e)
        assert "ds1" in msg
        assert "sid_abc" in msg


class TestCatchBehavior:
    def test_except_base_catches_all_subclasses(self):
        for cls in [NotFoundError, ValidationError, ServerError,
                    UserConfigServiceImmutableError, UnexpectedServiceTypeError,
                    A2XConnectionError, NotOwnedError]:
            with pytest.raises(A2XError):
                if cls is NotOwnedError:
                    raise cls("d", "s")
                else:
                    raise cls("m")

    def test_except_validation_catches_user_config_variant(self):
        """Callers that only know ValidationError still see the specialized one."""
        with pytest.raises(ValidationError):
            raise UserConfigServiceImmutableError("m", status_code=400)

    def test_except_http_does_not_catch_not_owned(self):
        """NotOwnedError must not be confused with a server error."""
        with pytest.raises(NotOwnedError):
            try:
                raise NotOwnedError("d", "s")
            except A2XHTTPError:
                pytest.fail("NotOwnedError caught by A2XHTTPError")
