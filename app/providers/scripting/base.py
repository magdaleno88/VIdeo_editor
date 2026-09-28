from typing import Protocol

from app.schemas.domain import (
    ScriptGenerationPayload,
    ScriptGenerationRequest,
    ScriptValidationPayload,
)


class ScriptGenerationProvider(Protocol):
    name: str
    model: str
    prompt_version: str

    def generate(
        self, context: dict, request: ScriptGenerationRequest, variant_index: int
    ) -> ScriptGenerationPayload: ...

    def validate(
        self, context: dict, script: ScriptGenerationPayload
    ) -> ScriptValidationPayload: ...

    def close(self) -> None: ...
