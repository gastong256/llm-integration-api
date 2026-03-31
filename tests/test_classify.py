import pytest
from pydantic import BaseModel

from app.services.classify_service import ClassifyService
from sdk.models import BaseModelWrapper


class FakeInput(BaseModel):
    input: str


class FakeOutput(BaseModel):
    label: str
    confidence: float


class FakeWrapper(BaseModelWrapper[FakeInput, FakeOutput]):
    name = "fake"
    version = "test"
    input_schema = FakeInput
    output_schema = FakeOutput

    def __init__(self) -> None:
        self.received_payload: FakeInput | None = None

    async def load(self) -> None:
        return None

    async def predict(self, payload: FakeInput) -> FakeOutput:
        self.received_payload = payload
        return FakeOutput(label="positive", confidence=0.75)

    async def health(self) -> bool:
        return True


class FakeRegistry:
    def __init__(self, wrapper: FakeWrapper) -> None:
        self._wrapper = wrapper

    def get(self, version: str) -> FakeWrapper:
        assert version == "test"
        return self._wrapper


@pytest.mark.asyncio
async def test_classify_returns_v1_result(async_client, valid_headers) -> None:
    response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v1"},
        json={"input": "payment approved"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["label"] == "positive"
    assert body["model_version"] == "v1"
    assert body["confidence"] > 0


@pytest.mark.asyncio
async def test_classify_service_uses_wrapper_schemas() -> None:
    wrapper = FakeWrapper()
    service = ClassifyService(FakeRegistry(wrapper))

    response = await service.classify("schema-bound payload", "test")

    assert wrapper.received_payload == FakeInput(input="schema-bound payload")
    assert response.label == "positive"
    assert response.confidence == 0.75
    assert response.model_version == "test"


@pytest.mark.asyncio
async def test_classify_returns_v2_result(async_client, valid_headers) -> None:
    v1_response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v1"},
        json={"input": "request not approved"},
    )
    v2_response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v2"},
        json={"input": "request not approved"},
    )

    assert v1_response.status_code == 200
    assert v2_response.status_code == 200
    assert v1_response.json()["label"] != v2_response.json()["label"]
    assert v2_response.json()["model_version"] == "v2"


@pytest.mark.asyncio
async def test_classify_returns_400_for_unknown_model_version(async_client, valid_headers) -> None:
    response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v3"},
        json={"input": "payment approved"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {"error": "model version not found: v3", "retry_after_s": None}
    }


@pytest.mark.asyncio
async def test_classify_defaults_to_v1(async_client, valid_headers) -> None:
    response = await async_client.post(
        "/v1/classify",
        headers=valid_headers,
        json={"input": "request not approved"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["label"] == "positive"
    assert body["model_version"] == "v1"
