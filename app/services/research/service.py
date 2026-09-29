import logging
import re

from app.core.database import utcnow
from app.core.errors import (
    ConflictError,
    DocumentFetchError,
    InsufficientEvidenceError,
    ResearchValidationError,
)
from app.models import (
    ResearchClaim,
    ResearchClaimEvidence,
    ResearchContradiction,
    ResearchDossier,
    ResearchSource,
)
from app.providers.research.base import ResearchAIProvider, WebSearchProvider
from app.repositories.candidates import CandidateRepository
from app.repositories.research import ResearchRepository, dossier_read
from app.schemas.domain import (
    CandidateStatus,
    ClaimStatus,
    EvidenceExtractionPayload,
    EvidenceRelation,
    KnowledgeType,
    ResearchDocument,
    ResearchDossierRead,
    ResearchReview,
    ResearchRunResponse,
    ResearchStatus,
    SourceTier,
    VideoVisualAnalysis,
)
from app.services.research.documents import SafeDocumentFetcher
from app.services.research.planning import ResearchPlanner
from app.services.research.sources import SourceRanker

logger = logging.getLogger(__name__)
RESEARCH_VERSION = "1.0"
QUANTITATIVE_PATTERN = re.compile(
    r"(?:\b\d+(?:[.,]\d+)?\b|%|°[CF]\b|\b(?:rpm|bar|psi|mpa|mm|cm|kg|tons?|seconds?|minutes?)\b)",
    re.IGNORECASE,
)


class TechnicalResearchService:
    def __init__(
        self,
        candidates: CandidateRepository,
        repository: ResearchRepository,
        search_provider: WebSearchProvider,
        document_fetcher: SafeDocumentFetcher,
        research_provider: ResearchAIProvider,
        planner: ResearchPlanner,
        ranker: SourceRanker,
        *,
        max_sources: int,
        max_pages: int,
        min_sources: int,
        max_llm_calls: int,
    ) -> None:
        self.candidates = candidates
        self.repository = repository
        self.search_provider = search_provider
        self.document_fetcher = document_fetcher
        self.research_provider = research_provider
        self.planner = planner
        self.ranker = ranker
        self.max_sources = max_sources
        self.max_pages = max_pages
        self.min_sources = min_sources
        self.max_llm_calls = max_llm_calls

    def research(self, candidate_id: int, *, force: bool = False) -> ResearchRunResponse:
        candidate = self.candidates.get(candidate_id)
        if candidate.status in (CandidateStatus.PROCESSING, CandidateStatus.READY):
            raise ConflictError("Production candidates cannot start technical research")
        evaluation = self.candidates.latest_by_method(candidate_id, "ai_visual")
        if evaluation is None:
            evaluation = self.candidates.latest_by_method(candidate_id, "source_structural")
        if evaluation is None or not evaluation.analysis:
            raise ConflictError("AI visual analysis is required before technical research")
        analysis = VideoVisualAnalysis.model_validate(evaluation.analysis)
        if not force:
            existing = self.repository.reusable(
                candidate_id,
                self.search_provider.name,
                self.research_provider.name,
                self.research_provider.model,
                self.research_provider.prompt_version,
                RESEARCH_VERSION,
            )
            if existing:
                return ResearchRunResponse(dossier=dossier_read(existing), reused=True)

        questions, queries = self.planner.build(analysis, candidate.object_being_manufactured)
        results = []
        for query in queries:
            results.extend(self.search_provider.search(query.query, self.max_sources))
        if not results:
            raise InsufficientEvidenceError("Research search returned no useful results")

        documents = self._fetch_documents(results)
        if len(documents) < self.min_sources:
            raise InsufficientEvidenceError(
                f"Research requires at least {self.min_sources} usable sources"
            )
        if self.max_llm_calls < 2:
            raise ConflictError("Research requires a budget of two structured AI calls")
        extraction = self.research_provider.extract_evidence(
            analysis,
            [item.model_dump(mode="json") for item in questions],
            documents,
        )
        self._validate_extraction(extraction, documents)
        synthesis = self.research_provider.verify_claims(analysis, extraction, documents)
        self._validate_synthesis(extraction, synthesis)
        dossier = self._persist(
            candidate_id,
            analysis,
            questions,
            queries,
            documents,
            extraction,
            synthesis,
        )
        self.candidates.record_event(
            candidate,
            "TECHNICAL_RESEARCH_COMPLETED",
            "system",
            "Versioned research dossier created; human verification is still required.",
            {"dossier_id": dossier.id, "research_version": RESEARCH_VERSION},
        )
        return ResearchRunResponse(dossier=dossier_read(dossier), reused=False)

    def _fetch_documents(self, results) -> list[ResearchDocument]:
        documents = []
        for result, tier, source_type, relevance, credibility in self.ranker.rank(results)[
            : self.max_pages
        ]:
            try:
                fetched = self.document_fetcher.fetch(str(result.url))
            except DocumentFetchError as exc:
                logger.warning(
                    "research_source_skipped",
                    extra={"error_type": type(exc).__name__},
                )
                continue
            documents.append(
                self.ranker.document(
                    result,
                    fetched,
                    tier,
                    source_type,
                    relevance,
                    credibility,
                )
            )
            if len(documents) >= self.max_sources:
                break
        return documents

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.split())

    def _validate_extraction(
        self, extraction: EvidenceExtractionPayload, documents: list[ResearchDocument]
    ) -> None:
        document_text = {
            str(item.url): self._normalize(item.content).casefold() for item in documents
        }
        for evidence in extraction.evidence:
            url = str(evidence.source_url)
            if url not in document_text:
                raise ResearchValidationError("Extracted evidence references an unknown source")
            if self._normalize(evidence.excerpt).casefold() not in document_text[url]:
                raise ResearchValidationError(
                    "Extracted evidence is not present in the referenced source"
                )

    @staticmethod
    def _validate_synthesis(extraction, synthesis) -> None:
        claims = {item.claim_key for item in extraction.candidate_claims}
        decisions = {item.claim_key for item in synthesis.claims}
        if decisions != claims:
            raise ResearchValidationError("Claim verification must decide every candidate claim")
        evidence = {item.evidence_id for item in extraction.evidence}
        for decision in synthesis.claims:
            refs = set(decision.supporting_evidence_ids + decision.contradicting_evidence_ids)
            if refs - evidence:
                raise ResearchValidationError("Claim verification references unknown evidence")
        for contradiction in synthesis.contradictions:
            if set(contradiction.claim_keys) - claims:
                raise ResearchValidationError("Contradiction references an unknown claim")
            if set(contradiction.evidence_ids) - evidence:
                raise ResearchValidationError("Contradiction references unknown evidence")
        decisions_by_key = {item.claim_key: item for item in synthesis.claims}
        sourced_items = [
            *synthesis.machine_types,
            *synthesis.process_steps,
            *synthesis.technical_explanations,
            *synthesis.interesting_facts,
            *synthesis.safety_notes,
        ]
        allowed = {ClaimStatus.VERIFIED, ClaimStatus.PARTIALLY_SUPPORTED}
        if any(
            decisions_by_key[key].status not in allowed
            for item in sourced_items
            for key in item.claim_keys
        ):
            raise ResearchValidationError("Synthesis facts must reference supported claims")
        candidate_by_key = {item.claim_key: item for item in extraction.candidate_claims}
        if synthesis.verified_process_name and not any(
            item.status == ClaimStatus.VERIFIED
            and candidate_by_key[item.claim_key].category.casefold() == "process"
            for item in synthesis.claims
        ):
            raise ResearchValidationError("Verified process name requires a verified process claim")
        if synthesis.material and not any(
            item.status in allowed
            and candidate_by_key[item.claim_key].category.casefold() == "material"
            for item in synthesis.claims
        ):
            raise ResearchValidationError("Material requires a supported material claim")

    def _persist(
        self,
        candidate_id,
        analysis,
        questions,
        queries,
        documents,
        extraction,
        synthesis,
    ) -> ResearchDossier:
        visual_observations = []
        for item in analysis.evidence:
            visual_observations.append(item.model_dump(mode="json"))
        for _, dimension in analysis.dimensions:
            visual_observations.extend(item.model_dump(mode="json") for item in dimension.evidence)
        evidence_by_id = {item.evidence_id: item for item in extraction.evidence}
        candidate_by_key = {item.claim_key: item for item in extraction.candidate_claims}
        source_policy_by_url = {str(item.url): item for item in documents}
        final_statuses = {
            decision.claim_key: self._policy_status(
                decision,
                candidate_by_key[decision.claim_key].statement,
                evidence_by_id,
                source_policy_by_url,
            )
            for decision in synthesis.claims
        }
        self._validate_policy_synthesis(synthesis, candidate_by_key, final_statuses)
        dossier = ResearchDossier(
            candidate_id=candidate_id,
            status=ResearchStatus.NEEDS_REVIEW,
            detected_object=analysis.detected_object,
            detected_process=analysis.detected_process,
            visual_observations=visual_observations[:100],
            alternate_process_hypotheses=extraction.alternate_process_hypotheses,
            research_questions=[item.model_dump(mode="json") for item in questions],
            search_queries=[item.model_dump(mode="json") for item in queries],
            research_summary=self._build_summary(synthesis, final_statuses),
            verified_process_name=synthesis.verified_process_name,
            process_confidence=synthesis.process_confidence,
            material=synthesis.material,
            machine_types=[item.model_dump(mode="json") for item in synthesis.machine_types],
            process_steps=[item.model_dump(mode="json") for item in synthesis.process_steps],
            technical_explanations=[
                item.model_dump(mode="json") for item in synthesis.technical_explanations
            ],
            interesting_facts=[
                item.model_dump(mode="json") for item in synthesis.interesting_facts
            ],
            safety_notes=[item.model_dump(mode="json") for item in synthesis.safety_notes],
            unknowns=synthesis.unknowns,
            search_provider=self.search_provider.name,
            research_provider=self.research_provider.name,
            model=self.research_provider.model,
            prompt_version=self.research_provider.prompt_version,
            research_version=RESEARCH_VERSION,
        )
        for document in documents:
            dossier.sources.append(
                ResearchSource(
                    url=str(document.url),
                    title=document.title,
                    publisher=document.publisher,
                    author=document.author,
                    publication_date=document.publication_date,
                    source_type=document.source_type,
                    tier=document.tier,
                    relevance=document.relevance,
                    credibility=document.credibility,
                    search_snippet=document.search_snippet,
                    notes="Fetched under bounded research policy.",
                )
            )
        self.repository.add(dossier)
        source_by_url = {item.url: item for item in dossier.sources}
        claim_by_key = {}
        for decision in synthesis.claims:
            candidate_claim = candidate_by_key[decision.claim_key]
            status = final_statuses[decision.claim_key]
            claim = ResearchClaim(
                statement=candidate_claim.statement,
                category=candidate_claim.category,
                status=status,
                knowledge_type=(
                    KnowledgeType.VERIFIED_FACT
                    if status == ClaimStatus.VERIFIED
                    else KnowledgeType.UNKNOWN
                    if status == ClaimStatus.UNVERIFIED
                    else KnowledgeType.INFERENCE
                ),
                source_support_confidence=decision.source_support_confidence,
                video_applicability_confidence=decision.video_applicability_confidence,
                quantitative=self.is_quantitative(candidate_claim.statement),
                notes=decision.notes,
            )
            dossier.claims.append(claim)
            claim_by_key[decision.claim_key] = claim
            for relation, evidence_ids in (
                (EvidenceRelation.SUPPORTS, decision.supporting_evidence_ids),
                (EvidenceRelation.CONTRADICTS, decision.contradicting_evidence_ids),
            ):
                for evidence_id in evidence_ids:
                    evidence = evidence_by_id[evidence_id]
                    claim.evidence.append(
                        ResearchClaimEvidence(
                            source=source_by_url[str(evidence.source_url)],
                            relation=relation,
                            excerpt=evidence.excerpt,
                            location=evidence.location,
                            source_says_confidence=evidence.source_says_confidence,
                        )
                    )
        self.repository.session.flush()
        for contradiction in synthesis.contradictions:
            used_evidence = [evidence_by_id[item] for item in contradiction.evidence_ids]
            dossier.contradictions.append(
                ResearchContradiction(
                    claim_ids=[claim_by_key[item].id for item in contradiction.claim_keys],
                    source_ids=sorted(
                        {source_by_url[str(item.source_url)].id for item in used_evidence}
                    ),
                    description=contradiction.description,
                    possible_explanations=contradiction.possible_explanations,
                    resolved=False,
                    notes="Unresolved; variants may explain the disagreement.",
                )
            )
        self.repository.session.flush()
        return dossier

    def _policy_status(
        self, decision, statement: str, evidence_by_id, source_by_url
    ) -> ClaimStatus:
        status = decision.status
        sources = [
            source_by_url[str(evidence_by_id[item].source_url)]
            for item in decision.supporting_evidence_ids
        ]
        if status == ClaimStatus.VERIFIED and not any(
            item.tier != SourceTier.TIER_D for item in sources
        ):
            return ClaimStatus.UNVERIFIED
        if (
            status == ClaimStatus.VERIFIED
            and self.is_quantitative(statement)
            and not any(item.tier in (SourceTier.TIER_A, SourceTier.TIER_B) for item in sources)
        ):
            return ClaimStatus.PARTIALLY_SUPPORTED if sources else ClaimStatus.UNVERIFIED
        return status

    @staticmethod
    def _validate_policy_synthesis(synthesis, candidate_by_key, final_statuses) -> None:
        allowed = {ClaimStatus.VERIFIED, ClaimStatus.PARTIALLY_SUPPORTED}
        sourced_items = [
            *synthesis.machine_types,
            *synthesis.process_steps,
            *synthesis.technical_explanations,
            *synthesis.interesting_facts,
            *synthesis.safety_notes,
        ]
        if any(
            final_statuses[key] not in allowed for item in sourced_items for key in item.claim_keys
        ):
            raise ResearchValidationError(
                "Synthesis facts cannot reference claims downgraded by source policy"
            )
        if synthesis.verified_process_name and not any(
            status == ClaimStatus.VERIFIED
            and candidate_by_key[key].category.casefold() == "process"
            for key, status in final_statuses.items()
        ):
            raise ResearchValidationError(
                "Verified process name lacks a policy-qualified process claim"
            )
        if synthesis.material and not any(
            status in allowed and candidate_by_key[key].category.casefold() == "material"
            for key, status in final_statuses.items()
        ):
            raise ResearchValidationError("Material lacks a policy-qualified material claim")

    @staticmethod
    def _build_summary(synthesis, final_statuses) -> str:
        counts = {status: 0 for status in ClaimStatus}
        for status in final_statuses.values():
            counts[status] += 1
        summary = (
            f"Claim review: {counts[ClaimStatus.VERIFIED]} verified, "
            f"{counts[ClaimStatus.PARTIALLY_SUPPORTED]} partially supported, "
            f"{counts[ClaimStatus.UNVERIFIED]} unverified, and "
            f"{counts[ClaimStatus.CONTRADICTED]} contradicted."
        )
        if synthesis.verified_process_name:
            summary += f" Supported process label: {synthesis.verified_process_name}."
        if synthesis.unknowns:
            summary += " Remaining unknowns are recorded separately."
        return summary

    @staticmethod
    def is_quantitative(statement: str) -> bool:
        return bool(QUANTITATIVE_PATTERN.search(statement))


class ResearchReviewService:
    def __init__(self, candidates: CandidateRepository, repository: ResearchRepository) -> None:
        self.candidates = candidates
        self.repository = repository

    def dossiers(self, candidate_id: int) -> list[ResearchDossierRead]:
        self.candidates.get(candidate_id)
        return [dossier_read(item) for item in self.repository.list(candidate_id)]

    def latest(self, candidate_id: int) -> ResearchDossierRead:
        self.candidates.get(candidate_id)
        dossier = self.repository.latest(candidate_id)
        if dossier is None:
            raise ConflictError("Candidate has no technical research dossier")
        return dossier_read(dossier)

    def review(self, candidate_id: int, review: ResearchReview) -> ResearchDossierRead:
        candidate = self.candidates.get(candidate_id)
        dossier = self.repository.latest(candidate_id)
        if dossier is None:
            raise ConflictError("Candidate has no technical research dossier")
        target = ResearchStatus(review.decision)
        if target == ResearchStatus.VERIFIED and not any(
            claim.status == ClaimStatus.VERIFIED for claim in dossier.claims
        ):
            raise ConflictError("A dossier needs at least one verified claim before approval")
        dossier.status = target
        dossier.reviewed_at = utcnow()
        dossier.reviewed_by = review.reviewer
        dossier.review_notes = review.notes
        self.candidates.record_event(
            candidate,
            "RESEARCH_VERIFIED" if target == ResearchStatus.VERIFIED else "RESEARCH_REJECTED",
            review.reviewer,
            review.notes,
            {"dossier_id": dossier.id, "status": target.value},
        )
        self.repository.session.flush()
        return dossier_read(dossier)
