import httpx
import pytest

from app.adapters.http import HttpLLMAdapter
from app.core.exceptions import UpstreamProviderError


async def _build_adapter(transport: httpx.MockTransport) -> HttpLLMAdapter:
    adapter = HttpLLMAdapter("http://provider.test", 5, api_key="secret")
    original_client = adapter._client
    adapter._client = httpx.AsyncClient(
        base_url="http://provider.test",
        timeout=5,
        headers=original_client.headers,
        transport=transport,
    )
    await original_client.aclose()
    return adapter


@pytest.mark.asyncio
async def test_http_adapter_infer_maps_httpx_timeout_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    adapter = await _build_adapter(httpx.MockTransport(handler))

    with pytest.raises(TimeoutError, match="LLM provider timeout"):
        await adapter.infer("gpt-4o-mini", "hello", None)

    await adapter.aclose()


@pytest.mark.asyncio
async def test_http_adapter_stream_maps_httpx_timeout_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    adapter = await _build_adapter(httpx.MockTransport(handler))

    with pytest.raises(TimeoutError, match="LLM provider timeout"):
        async for _ in adapter.stream("gpt-4o-mini", "hello", None):
            pass

    await adapter.aclose()


@pytest.mark.asyncio
async def test_http_adapter_infer_maps_http_status_error_to_upstream_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, request=request, json={"error": "bad gateway"})

    adapter = await _build_adapter(httpx.MockTransport(handler))

    with pytest.raises(UpstreamProviderError, match="upstream provider error: 502"):
        await adapter.infer("gpt-4o-mini", "hello", None)

    await adapter.aclose()


@pytest.mark.asyncio
async def test_http_adapter_stream_maps_http_status_error_to_upstream_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, json={"error": "unavailable"})

    adapter = await _build_adapter(httpx.MockTransport(handler))

    with pytest.raises(UpstreamProviderError, match="upstream provider error: 503"):
        async for _ in adapter.stream("gpt-4o-mini", "hello", None):
            pass

    await adapter.aclose()


@pytest.mark.asyncio
async def test_http_adapter_health_check_returns_false_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    adapter = await _build_adapter(httpx.MockTransport(handler))

    assert await adapter.health_check() is False

    await adapter.aclose()
