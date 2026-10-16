"""Invalid or obviously sensitive payloads must fail before database access."""

from unittest.mock import Mock

import pytest

from app.core.exceptions import ValidationError
from app.services.execution_service import ExecutionService


@pytest.mark.parametrize(
    "payload",
    [
        {"nested": {"access_token": "fake-secret"}},
        {"password": "fake-secret"},
        {"large": "x" * 17000},
        {"invalid": float("nan")},
        {"invalid": float("inf")},
        {"invalid": "\u0000"},
        {"invalid": object()},
        [1, 2],
    ],
)
def test_bad_payload_cannot_reach_database(payload):
    factory = Mock(kw={})
    service = ExecutionService(factory)
    with pytest.raises(ValidationError):
        service.succeed(None, payload)
    factory.assert_not_called()


def test_exception_text_cannot_be_saved_as_error_code():
    factory = Mock(kw={})
    service = ExecutionService(factory)
    with pytest.raises(ValidationError) as error:
        service.fail(None, "password=fake-secret URL=postgresql://fake")
    assert "fake-secret" not in str(error.value)
    factory.assert_not_called()
