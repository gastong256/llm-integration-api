import re
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

PUNCTUATION_RE = re.compile(r"[^\w\s']")
WHITESPACE_RE = re.compile(r"\s+")

TRAINING_DATA: list[tuple[str, str]] = [
    ("positive", " Pricing looks accurate "),
    ("positive", "promotion looks effective"),
    ("positive", "STOCK looks healthy"),
    ("positive", "inventory looks stable."),
    ("positive", "assortment looks strong"),
    ("positive", "shelf execution looks good!!"),
    ("positive", "store data looks reliable"),
    ("positive", "ticket feed looks clean"),
    ("positive", "price-sync was successful"),
    ("positive", "promotion result was positive"),
    ("positive", "pricing is not wrong"),
    ("positive", "promotion is not weak"),
    ("positive", "stock is not low"),
    ("positive", "inventory is not delayed"),
    ("positive", "store data is not bad"),
    ("negative", "pricing looks wrong"),
    ("negative", "promotion looks weak"),
    ("negative", " stock looks low "),
    ("negative", "inventory looks unstable"),
    ("negative", "assortment looks poor"),
    ("negative", "shelf execution looks bad"),
    ("negative", "store data looks unreliable!!!"),
    ("negative", "ticket feed looks noisy"),
    ("negative", "price sync failed"),
    ("negative", "promotion result was negative"),
    ("negative", "pricing is not accurate"),
    ("negative", "promotion is not effective"),
    ("negative", "stock is not healthy"),
    ("negative", "inventory is not stable"),
    ("negative", "Store data is not clean"),
]


def normalize_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    normalized = WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def build_pipeline(ngram_range: tuple[int, int], c_value: float = 1.0) -> Pipeline:
    return Pipeline(
        steps=[
            ("vectorizer", TfidfVectorizer(ngram_range=ngram_range)),
            (
                "classifier",
                LogisticRegression(random_state=42, max_iter=500, C=c_value),
            ),
        ]
    )


def train_and_save(model_path: Path, pipeline: Pipeline) -> None:
    labels = [label for label, _ in TRAINING_DATA]
    texts = [normalize_text(text) for _, text in TRAINING_DATA]
    pipeline.fit(texts, labels)
    joblib.dump(pipeline, model_path)


def main() -> None:
    models_dir = Path(__file__).resolve().parents[1] / "models"
    models_dir.mkdir(exist_ok=True)

    train_and_save(models_dir / "v1.joblib", build_pipeline((1, 1)))
    train_and_save(models_dir / "v2.joblib", build_pipeline((1, 2), c_value=0.5))


if __name__ == "__main__":
    main()
