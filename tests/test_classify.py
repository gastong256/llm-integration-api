import pytest
from pydantic import BaseModel

from app.models.text_processing import build_output, normalize_text
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
        json={"input": "pricing looks accurate"},
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
async def test_classify_service_carries_trace_correlation_into_logs(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    wrapper = FakeWrapper()
    service = ClassifyService(FakeRegistry(wrapper))

    monkeypatch.setattr(
        "app.services.classify_service.get_trace_correlation",
        lambda: {"trace_id": "a" * 32, "span_id": "b" * 16},
    )
    monkeypatch.setattr(
        "app.services.classify_service.logger.info",
        lambda event, **kwargs: events.append({"event": event, **kwargs}),
    )

    response = await service.classify("schema-bound payload", "test", request_id="req-123")

    assert response.model_version == "test"
    assert events == [
        {
            "event": "classify_complete",
            "model_version": "test",
            "label": "positive",
            "latency_ms": response.latency_ms,
            "input": "schema-bound payload",
            "status": "success",
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
        }
    ]


@pytest.mark.asyncio
async def test_classify_returns_v2_result(async_client, valid_headers) -> None:
    v1_response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v1"},
        json={"input": "pricing is not accurate"},
    )
    v2_response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v2"},
        json={"input": "pricing is not accurate"},
    )

    assert v1_response.status_code == 200
    assert v2_response.status_code == 200
    assert v1_response.json()["label"] != v2_response.json()["label"]
    assert v2_response.json()["model_version"] == "v2"


@pytest.mark.asyncio
async def test_classify_normalizes_input_inside_wrapper(async_client, valid_headers) -> None:
    response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v2"},
        json={"input": "  PRICING   is not accurate!!!  "},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["label"] == "negative"
    assert body["model_version"] == "v2"


@pytest.mark.asyncio
async def test_classify_returns_400_for_unknown_model_version(async_client, valid_headers) -> None:
    response = await async_client.post(
        "/v1/classify",
        headers={**valid_headers, "X-Model-Version": "v3"},
        json={"input": "pricing looks accurate"},
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
        json={"input": "pricing is not accurate"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["label"] == "positive"
    assert body["model_version"] == "v1"


def test_normalize_text_uses_model_layer_preprocessing() -> None:
    assert normalize_text("  PRICING   is not accurate!!!  ") == "pricing is not accurate"


def test_build_output_normalizes_label_and_confidence() -> None:
    output = build_output(" NEGATIVE ", 1.2)

    assert output.label == "negative"
    assert output.confidence == 1.0
