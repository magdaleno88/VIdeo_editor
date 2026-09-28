from typing import Protocol

from app.schemas.domain import (
    EvidenceExtractionPayload,
    ResearchDocument,
    ResearchSynthesisPayload,
    SearchResult,
    VideoVisualAnalysis,
)


class WebSearchProvider(Protocol):
    name: str

    def search(self, query: str, count: int) -> list[SearchResult]: ...


class ResearchAIProvider(Protocol):
    name: str
    model: str
    prompt_version: str

    def extract_evidence(
        self,
        analysis: VideoVisualAnalysis,
        questions: list[dict[str, str]],
        documents: list[ResearchDocument],
    ) -> EvidenceExtractionPayload: ...

    def verify_claims(
        self,
        analysis: VideoVisualAnalysis,
        extraction: EvidenceExtractionPayload,
        documents: list[ResearchDocument],
    ) -> ResearchSynthesisPayload: ...

    def close(self) -> None: ...
