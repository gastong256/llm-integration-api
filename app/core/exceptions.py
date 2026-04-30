class RateLimitExceeded(Exception):
    def __init__(
        self,
        retry_after_s: float,
        limit: int,
        remaining: int,
        reset_s: int,
    ) -> None:
        self.retry_after_s = retry_after_s
        self.limit = limit
        self.remaining = remaining
        self.reset_s = reset_s
        super().__init__(f"rate limit exceeded, retry after {retry_after_s}s")


class CircuitOpenError(Exception):
    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(f"circuit open, retry after {retry_after_s}s")


class UpstreamProviderError(Exception):
    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        if status_code is None:
            super().__init__("upstream provider error")
        else:
            super().__init__(f"upstream provider error: {status_code}")


class ModelVersionNotFound(Exception):
    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(f"model version not found: {version}")
