class CircuitOpenError(Exception):
    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(f"circuit open, retry after {retry_after_s}s")


class ModelVersionNotFound(Exception):
    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(f"model version not found: {version}")
