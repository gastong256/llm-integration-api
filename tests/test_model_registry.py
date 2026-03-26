from pathlib import Path

import pytest

from app.core.exceptions import ModelVersionNotFound
from app.core.model_registry import ModelRegistry


@pytest.mark.asyncio
async def test_model_registry_loads_both_versions() -> None:
    registry = ModelRegistry(Path("models/v1.joblib"), Path("models/v2.joblib"))

    loaded_versions = await registry.load_all()

    assert loaded_versions == ["v1", "v2"]
    assert registry.loaded_versions == ["v1", "v2"]


@pytest.mark.asyncio
async def test_model_registry_raises_for_unknown_version() -> None:
    registry = ModelRegistry(Path("models/v1.joblib"), Path("models/v2.joblib"))
    await registry.load_all()

    with pytest.raises(ModelVersionNotFound):
        await registry.predict("v3", "request approved")
