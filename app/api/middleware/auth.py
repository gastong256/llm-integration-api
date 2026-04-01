import structlog
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.core.settings import Settings, get_settings

_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    x_api_key: str | None = Depends(_header_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    if not x_api_key or x_api_key not in settings.api_keys_set:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    structlog.contextvars.bind_contextvars(client_id=x_api_key)
    return x_api_key
