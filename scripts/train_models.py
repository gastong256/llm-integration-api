from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

TRAINING_DATA: list[tuple[str, str]] = [
    ("positive", "payment approved"),
    ("positive", "order approved"),
    ("positive", "request was accepted"),
    ("positive", "shipment not delayed"),
    ("positive", "account not blocked"),
    ("positive", "refund was accepted"),
    ("positive", "invoice not rejected"),
    ("positive", "customer access granted"),
    ("positive", "package arrived on time"),
    ("positive", "service is working properly"),
    ("negative", "payment rejected"),
    ("negative", "order rejected"),
    ("negative", "request not approved"),
    ("negative", "shipment delayed"),
    ("negative", "account blocked"),
    ("negative", "refund was denied"),
    ("negative", "invoice not accepted"),
    ("negative", "customer access revoked"),
    ("negative", "package arrived late"),
    ("negative", "service is not working"),
]


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
    texts = [text for _, text in TRAINING_DATA]
    pipeline.fit(texts, labels)
    joblib.dump(pipeline, model_path)


def main() -> None:
    models_dir = Path(__file__).resolve().parents[1] / "models"
    models_dir.mkdir(exist_ok=True)

    train_and_save(models_dir / "v1.joblib", build_pipeline((1, 1)))
    train_and_save(models_dir / "v2.joblib", build_pipeline((1, 2), c_value=0.5))


if __name__ == "__main__":
    main()
