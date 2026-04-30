import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import use_span

from app.core.observability import (
    add_trace_correlation,
    get_trace_correlation,
    log_safe_config,
    log_safe_input,
    log_safe_output,
    sanitize_for_log,
    truncate_text,
)
from app.core.circuit_breaker import CircuitBreaker
from app.main import _metric_path_label, _status_class_label
from app.services.inference_service import InferenceService


class AllowingRateLimiter:
    limit = 60

    async def check(self, client_id: str) -> tuple[bool, float, int, int]:
        return True, 0.0, 59, 60


class RejectingRateLimiter:
    limit = 60

    async def check(self, client_id: str) -> tuple[bool, float, int, int]:
        return False, 12.4, 0, 12


class InMemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, dict[str, object]] = {}

    def make_key(self, model: str, input: str, config: dict[str, object] | None) -> str:
        return repr((model, input, config))

    async def get(
        self, model: str, input: str, config: dict[str, object] | None
    ) -> dict[str, object] | None:
        return self._values.get(self.make_key(model, input, config))

    async def set(
        self,
        model: str,
        input: str,
        config: dict[str, object] | None,
        value: dict[str, object],
    ) -> None:
        self._values[self.make_key(model, input, config)] = value


class UnusedInferenceService:
    async def infer(self, request_id: str, req) -> None:
        raise AssertionError("inference service should not be called")


def test_add_trace_correlation_skips_when_no_active_span() -> None:
    event = {"event": "test"}

    enriched = add_trace_correlation(None, "info", event)

    assert enriched == {"event": "test"}


def test_add_trace_correlation_adds_trace_and_span_ids() -> None:
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("demo") as span:
        with use_span(span, end_on_exit=False):
            enriched = add_trace_correlation(None, "info", {"event": "test"})

    assert enriched["event"] == "test"
    assert len(enriched["trace_id"]) == 32
    assert len(enriched["span_id"]) == 16
    assert int(enriched["trace_id"], 16) > 0
    assert int(enriched["span_id"], 16) > 0


def test_get_trace_correlation_skips_when_no_active_span() -> None:
    assert get_trace_correlation() == {}


def test_get_trace_correlation_returns_trace_and_span_ids() -> None:
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("demo"):
        correlation = get_trace_correlation()

    assert len(correlation["trace_id"]) == 32
    assert len(correlation["span_id"]) == 16


def test_truncate_text_keeps_a_bounded_prefix() -> None:
    value = "x" * 130

    truncated = truncate_text(value, limit=12)

    assert truncated == "xxxxxxxxx..."


def test_sanitize_for_log_masks_obvious_keys_and_truncates_strings() -> None:
    payload = {
        "input": "A" * 20,
        "api_key": "secret",
        "nested": {
            "Authorization": "Bearer secret",
            "token": "nested-secret",
            "notes": "B" * 20,
        },
        "list": ["C" * 20, {"refresh_token": "refresh-secret"}],
    }

    sanitized = sanitize_for_log(payload, text_limit=12)

    assert sanitized == {
        "input": "AAAAAAAAA...",
        "api_key": "***",
        "nested": {
            "Authorization": "***",
            "token": "***",
            "notes": "BBBBBBBBB...",
        },
        "list": ["CCCCCCCCC...", {"refresh_token": "***"}],
    }


def test_log_safe_helpers_keep_safe_logging_semantics_explicit() -> None:
    assert log_safe_input("A" * 20) == "A" * 20
    assert log_safe_output("B" * 130) == ("B" * 117) + "..."
    assert log_safe_config({"api_key": "secret", "temperature": 0.2}) == {
        "api_key": "***",
        "temperature": 0.2,
    }


def test_metric_path_label_stays_low_cardinality() -> None:
    assert _metric_path_label("/health") == "/health"
    assert _metric_path_label("/v1/infer") == "/v1/infer"
    assert _metric_path_label("/v1/classify") == "/v1/classify"
    assert _metric_path_label("/metrics") == "other"
    assert _metric_path_label("/unknown/path") == "other"


def test_status_class_label_groups_status_codes() -> None:
    assert _status_class_label(200) == "2xx"
    assert _status_class_label(429) == "4xx"
    assert _status_class_label(503) == "5xx"


@pytest.mark.asyncio
async def test_metrics_include_http_request_breakdown_for_success_paths(
    async_client, app_instance, valid_headers, stub_adapter
) -> None:
    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.inference_service = InferenceService(
        stub_adapter,
        InMemoryCache(),
        CircuitBreaker(),
    )

    await async_client.get("/health")
    await async_client.post(
        "/v1/infer",
        headers=valid_headers,
        json={"model": "gpt-4o-mini", "input": "observability metrics"},
    )
    await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v1"},
        json={"input": "pricing looks accurate"},
    )

    metrics = (await async_client.get("/metrics")).text

    assert 'llm_api_http_requests_total{path="/health",status_class="2xx"}' in metrics
    assert 'llm_api_http_requests_total{path="/v1/infer",status_class="2xx"}' in metrics
    assert 'llm_api_http_requests_total{path="/v1/classify",status_class="2xx"}' in metrics


@pytest.mark.asyncio
async def test_metrics_include_http_request_breakdown_for_429(async_client, app_instance) -> None:
    app_instance.state.rate_limiter = RejectingRateLimiter()
    app_instance.state.inference_service = UnusedInferenceService()

    await async_client.post(
        "/v1/infer",
        headers={"X-API-Key": "test-key-1"},
        json={"model": "gpt-4o-mini", "input": "too many"},
    )

    metrics = (await async_client.get("/metrics")).text

    assert 'llm_api_http_requests_total{path="/v1/infer",status_class="4xx"}' in metrics
