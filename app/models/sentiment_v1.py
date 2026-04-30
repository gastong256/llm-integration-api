from pathlib import Path

from app.models.sentiment_wrapper import BaseSentimentWrapper


class SentimentV1(BaseSentimentWrapper):
    version = "v1"

    def __init__(self, model_path: Path) -> None:
        super().__init__(model_path)
