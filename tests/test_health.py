import pytest


class HealthyRedis:
    async def ping(self) -> None:
        return None


@pytest.mark.asyncio
async def test_health_returns_ok_without_auth(async_client, app_instance) -> None:
    app_instance.state.redis_client = HealthyRedis()

    response = await async_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "redis": "ok",
        "llm_circuit": "closed",
        "models_loaded": ["v1", "v2"],
    }
