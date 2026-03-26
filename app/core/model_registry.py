import asyncio
from pathlib import Path
from typing import Any, cast

import joblib
from sklearn.pipeline import Pipeline

from app.core.exceptions import ModelVersionNotFound


class ModelRegistry:
    def __init__(self, v1_path: Path, v2_path: Path) -> None:
        self._model_paths: dict[str, Path] = {"v1": v1_path, "v2": v2_path}
        self._models: dict[str, Pipeline] = {}

    @property
    def loaded_versions(self) -> list[str]:
        return list(self._models.keys())

    async def load_all(self) -> list[str]:
        versions = list(self._model_paths.keys())
        loaded_models = await asyncio.gather(
            *(asyncio.to_thread(joblib.load, self._model_paths[version]) for version in versions)
        )
        self._models = {
            version: cast(Pipeline, model) for version, model in zip(versions, loaded_models)
        }
        return self.loaded_versions

    async def predict(self, version: str, text: str) -> tuple[str, float]:
        model = self._models.get(version)
        if model is None:
            raise ModelVersionNotFound(version)
        return await asyncio.to_thread(self._predict_sync, model, text)

    def _predict_sync(self, model: Pipeline, text: str) -> tuple[str, float]:
        probabilities: Any = model.predict_proba([text])[0]
        label = str(model.predict([text])[0])
        confidence = float(max(probabilities))
        return label, confidence
