from pathlib import Path

import pytest

from app.core.exceptions import ModelVersionNotFound
from app.core.model_registry import ModelRegistry
from app.models import SentimentV1
from app.models.schemas import TextInput
from sdk.models import BaseModelWrapper, WrapperRegistry


@pytest.mark.asyncio
async def test_model_registry_loads_both_versions() -> None:
    registry = ModelRegistry(Path("models"))

    loaded_versions = await registry.load_all()

    assert loaded_versions == ["v1", "v2"]
    assert registry.loaded_versions == ["v1", "v2"]
    assert await registry.get("v1").health() is True
    assert await registry.get("v2").health() is True


@pytest.mark.asyncio
async def test_model_registry_raises_for_unknown_version() -> None:
    registry = ModelRegistry(Path("models"))
    await registry.load_all()

    with pytest.raises(ModelVersionNotFound):
        registry.get("v3")


@pytest.mark.asyncio
async def test_model_registry_returns_wrappers() -> None:
    registry = ModelRegistry(Path("models"))
    await registry.load_all()

    wrapper = registry.get("v1")
    result = await wrapper.predict(TextInput(input="payment approved"))

    assert isinstance(wrapper, BaseModelWrapper)
    assert wrapper.name == "sentiment"
    assert wrapper.version == "v1"
    assert result.label == "positive"
    assert result.confidence > 0


def test_wrapper_registry_rejects_duplicate_versions() -> None:
    registry = WrapperRegistry()
    registry.register(SentimentV1(Path("models/v1.joblib")))

    with pytest.raises(ValueError, match="wrapper version already registered: v1"):
        registry.register(SentimentV1(Path("models/v1.joblib")))
