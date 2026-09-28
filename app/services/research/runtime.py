from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.providers.research import BraveWebSearchProvider, GeminiResearchProvider
from app.repositories.candidates import CandidateRepository
from app.repositories.research import ResearchRepository
from app.services.research.documents import SafeDocumentFetcher
from app.services.research.planning import ResearchPlanner
from app.services.research.service import TechnicalResearchService
from app.services.research.sources import SourceRanker


@contextmanager
def configured_research_service(
    settings: Settings, session: Session
) -> Iterator[TechnicalResearchService]:
    brave_key = settings.brave_search_api_key.get_secret_value().strip()
    gemini_key = settings.gemini_api_key.get_secret_value().strip()
    model = settings.gemini_research_model.strip()
    if not brave_key:
        raise ConfigurationError("BRAVE_SEARCH_API_KEY is required for technical research")
    if not gemini_key:
        raise ConfigurationError("GEMINI_API_KEY is required for technical research")
    if not model:
        raise ConfigurationError("GEMINI_RESEARCH_MODEL is required for technical research")
    with httpx.Client(
        timeout=settings.research_http_timeout_seconds, follow_redirects=False
    ) as client:
        provider = GeminiResearchProvider(
            gemini_key,
            model,
            settings.research_http_timeout_seconds,
        )
        try:
            yield TechnicalResearchService(
                CandidateRepository(session),
                ResearchRepository(session),
                BraveWebSearchProvider(brave_key, client),
                SafeDocumentFetcher(client, settings.research_max_document_bytes),
                provider,
                ResearchPlanner(settings.research_max_search_queries),
                SourceRanker(),
                max_sources=settings.research_max_sources,
                max_pages=settings.research_max_pages,
                min_sources=settings.research_min_sources,
                max_llm_calls=settings.research_max_llm_calls,
            )
        finally:
            provider.close()
