from collections.abc import MutableMapping
from typing import Any

from opentelemetry import trace

DEMO_TEXT_LIMIT = 120
REDACTED_VALUE = "***"
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "token",
    "access_token",
    "refresh_token",
}


def add_trace_correlation(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    del logger, method_name

    correlation = get_trace_correlation()
    if not correlation:
        return event_dict

    event_dict.update(correlation)
    return event_dict


def truncate_text(value: str, limit: int = DEMO_TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return f"{value[: limit - 3]}..."


def get_trace_correlation() -> dict[str, str]:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": f"{span_context.trace_id:032x}",
        "span_id": f"{span_context.span_id:016x}",
    }


def sanitize_for_log(value: Any, text_limit: int = DEMO_TEXT_LIMIT) -> Any:
    if isinstance(value, str):
        return truncate_text(value, limit=text_limit)

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = key.lower().replace("-", "_")
            if normalized_key in _SENSITIVE_KEYS:
                sanitized[key] = REDACTED_VALUE
            else:
                sanitized[key] = sanitize_for_log(item, text_limit=text_limit)
        return sanitized

    if isinstance(value, list):
        return [sanitize_for_log(item, text_limit=text_limit) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_for_log(item, text_limit=text_limit) for item in value)

    return value


def log_safe_input(value: str) -> str:
    return sanitize_for_log(value)


def log_safe_output(value: str) -> str:
    return sanitize_for_log(value)


def log_safe_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    return sanitize_for_log(config)
