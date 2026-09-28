import logging

from app.core.errors import ProviderError
from app.providers.base import VideoSourceProvider
from app.providers.registry import select_providers
from app.repositories.cache import SearchCacheRepository
from app.repositories.candidates import CandidateRepository, apply_score
from app.schemas.domain import (
    CandidateStatus,
    DiscoveryIssue,
    DiscoveryRequest,
    DiscoveryResult,
    ProviderName,
)
from app.services.discovery.queries import QueryGenerator
from app.services.scoring.scorers import VideoScorer

logger = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(
        self,
        repository: CandidateRepository,
        cache: SearchCacheRepository,
        providers: dict[ProviderName, VideoSourceProvider],
        generator: QueryGenerator,
        scorer: VideoScorer,
    ) -> None:
        self.repository = repository
        self.cache = cache
        self.providers = providers
        self.generator = generator
        self.scorer = scorer

    def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        providers = select_providers(self.providers, request.providers)
        queries = self.generator.generate(request)[: request.max_queries]
        result = DiscoveryResult(
            queries=queries,
            candidate_ids=[],
            created=0,
            duplicates=0,
            cache_hits=0,
            successful_searches=0,
            errors=[],
            warnings=[],
        )
        for provider in providers:
            for query in queries:
                key = self.cache.key(provider.name, query, request.page, request.per_page)
                page = self.cache.get(key)
                if page is not None:
                    result.cache_hits += 1
                else:
                    try:
                        page = provider.search(query, page=request.page, per_page=request.per_page)
                    except ProviderError as exc:
                        result.errors.append(
                            DiscoveryIssue(provider=provider.name, query=query, message=str(exc))
                        )
                        logger.warning("provider_search_failed", extra={"provider": provider.name})
                        # Stop this provider after any failure: no quota retry storm.
                        break
                    self.cache.put(key, page)
                result.successful_searches += 1
                if page.skipped:
                    result.warnings.append(
                        DiscoveryIssue(
                            provider=provider.name,
                            query=query,
                            message=f"Skipped {page.skipped} malformed records",
                        )
                    )
                for video in page.items:
                    candidate, created = self.repository.add_if_new(video, query, request)
                    if created:
                        score = self.scorer.score(video)
                        apply_score(candidate, score)
                        evaluation = self.repository.add_evaluation(candidate, score)
                        candidate.status = CandidateStatus.SCORED
                        self.repository.record_event(
                            candidate,
                            "DISCOVERED_AND_SCORED",
                            "system",
                            "Provider metadata imported; visual analysis not performed.",
                            {
                                "score": score.model_dump(mode="json"),
                                "evaluation_id": evaluation.id,
                            },
                        )
                        result.created += 1
                    else:
                        result.duplicates += 1
                    if candidate.id not in result.candidate_ids:
                        result.candidate_ids.append(candidate.id)
        if not result.successful_searches and result.errors:
            raise ProviderError(
                "All selected providers failed: " + "; ".join(e.message for e in result.errors)
            )
        logger.info(
            "discovery_completed",
            extra={"candidates_created": result.created, "duplicates": result.duplicates},
        )
        return result
