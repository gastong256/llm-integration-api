from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

InputModelT = TypeVar("InputModelT", bound=BaseModel)
OutputModelT = TypeVar("OutputModelT", bound=BaseModel)


class BaseModelWrapper(ABC, Generic[InputModelT, OutputModelT]):
    name: str
    version: str
    input_schema: type[InputModelT]
    output_schema: type[OutputModelT]

    @abstractmethod
    async def load(self) -> None: ...

    @abstractmethod
    async def predict(self, payload: InputModelT) -> OutputModelT: ...

    @abstractmethod
    async def health(self) -> bool: ...
