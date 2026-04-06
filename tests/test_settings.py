import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def test_settings_reject_invalid_adapter() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_adapter="other")


def test_settings_reject_invalid_stub_failure_rate() -> None:
    with pytest.raises(ValidationError):
        Settings(stub_failure_rate=1.5)


def test_settings_reject_non_positive_timeouts_and_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_timeout=0)

    with pytest.raises(ValidationError):
        Settings(cache_ttl=0)

    with pytest.raises(ValidationError):
        Settings(rate_limit_rpm=0)
