from pydantic import BaseModel


class TextInput(BaseModel):
    input: str


class ClassificationOutput(BaseModel):
    label: str
    confidence: float
