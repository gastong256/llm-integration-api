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
