import re

from app.models.schemas import ClassificationOutput

PUNCTUATION_RE = re.compile(r"[^\w\s']")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    # Pandas or heavier preprocessing can live here for more complex or batch cases.
    normalized = text.strip().lower()
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    normalized = WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def build_output(label: str, confidence: float) -> ClassificationOutput:
    normalized_label = label.strip().lower()
    normalized_confidence = max(0.0, min(1.0, confidence))
    return ClassificationOutput(
        label=normalized_label,
        confidence=normalized_confidence,
    )
