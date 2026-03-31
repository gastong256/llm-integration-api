from pathlib import Path

from app.models.sentiment_wrapper import BaseSentimentWrapper


class SentimentV2(BaseSentimentWrapper):
    version = "v2"

    def __init__(self, model_path: Path) -> None:
        super().__init__(model_path)
