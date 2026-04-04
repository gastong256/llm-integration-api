from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import use_span

from app.core.observability import add_trace_correlation


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
