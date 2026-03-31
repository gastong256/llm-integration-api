import asyncio
from pathlib import Path
from typing import Any, cast

import joblib
from sklearn.pipeline import Pipeline

from app.models.schemas import ClassificationOutput, TextInput
from sdk.models import BaseModelWrapper


class BaseSentimentWrapper(BaseModelWrapper[TextInput, ClassificationOutput]):
    name = "sentiment"
    input_schema = TextInput
    output_schema = ClassificationOutput

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        self._model: Pipeline | None = None

    async def load(self) -> None:
        self._model = await asyncio.to_thread(self._load_sync)

    async def predict(self, payload: TextInput) -> ClassificationOutput:
        model = self._require_model()
        return await asyncio.to_thread(self._predict_sync, model, payload.input)

    async def health(self) -> bool:
        return self._model is not None

    def _load_sync(self) -> Pipeline:
        return cast(Pipeline, joblib.load(self._model_path))

    def _predict_sync(self, model: Pipeline, text: str) -> ClassificationOutput:
        probabilities: Any = model.predict_proba([text])[0]
        label = str(model.predict([text])[0])
        return ClassificationOutput(label=label, confidence=float(max(probabilities)))

    def _require_model(self) -> Pipeline:
        if self._model is None:
            raise RuntimeError(f"{self.version} wrapper is not loaded")
        return self._model
