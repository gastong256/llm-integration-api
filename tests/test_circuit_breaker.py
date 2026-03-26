import pytest

from app.core.circuit_breaker import CircuitBreaker, State


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_failure_threshold() -> None:
    circuit_breaker = CircuitBreaker()

    for _ in range(5):
        await circuit_breaker.record_failure()

    assert circuit_breaker.state is State.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_rejects_requests_while_open() -> None:
    circuit_breaker = CircuitBreaker()

    for _ in range(5):
        await circuit_breaker.record_failure()

    assert await circuit_breaker.is_open() is True


@pytest.mark.asyncio
async def test_circuit_breaker_transitions_to_half_open_after_timeout(monkeypatch) -> None:
    current_time = 100.0
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: current_time)
    circuit_breaker = CircuitBreaker(recovery_timeout=60.0)

    for _ in range(5):
        await circuit_breaker.record_failure()

    current_time = 161.0

    assert await circuit_breaker.is_open() is False
    assert circuit_breaker.state is State.HALF_OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_closes_after_success_in_half_open(monkeypatch) -> None:
    current_time = 200.0
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: current_time)
    circuit_breaker = CircuitBreaker(recovery_timeout=60.0)

    for _ in range(5):
        await circuit_breaker.record_failure()

    current_time = 261.0
    await circuit_breaker.is_open()
    await circuit_breaker.record_success()

    assert circuit_breaker.state is State.CLOSED
    assert await circuit_breaker.is_open() is False


@pytest.mark.asyncio
async def test_circuit_breaker_retry_after_is_dynamic(monkeypatch) -> None:
    current_time = 300.0
    monkeypatch.setattr("app.core.circuit_breaker.time.monotonic", lambda: current_time)
    circuit_breaker = CircuitBreaker(recovery_timeout=60.0)

    for _ in range(5):
        await circuit_breaker.record_failure()

    current_time = 315.0

    assert circuit_breaker.get_retry_after() == pytest.approx(45.0)
