from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import ScoringWeights
from app.core.errors import (
    ConflictError,
    InsufficientEvidenceError,
    ResearchValidationError,
    SearchProviderError,
)
from app.repositories.candidates import CandidateRepository, apply_score
from app.repositories.research import ResearchRepository
from app.schemas.domain import (
    CandidateStatus,
    ClaimStatus,
    ClaimVerificationDecision,
    EvidenceExtractionPayload,
    Idea,
    NormalizedVideo,
    ResearchReview,
    ResearchSynthesisPayload,
    ScoreInputs,
    ScoreResult,
    SearchResult,
    SourceTier,
    VideoVisualAnalysis,
    VideoVisualAnalysisPayload,
)
from app.services.research.documents import FetchedDocument
from app.services.research.planning import ResearchPlanner
from app.services.research.service import ResearchReviewService, TechnicalResearchService
from app.services.research.sources import SourceRanker
from tests.test_ai_scoring import analysis_payload

NIST_URL = "https://nist.gov/thread-rolling"
ASME_URL = "https://asme.org/forming"
NIST_TEXT = (
    "Thread rolling forms threads through plastic deformation. "
    "A documented machine example produces 100 parts per minute."
)
ASME_TEXT = (
    "Rolling dies form the thread without cutting. "
    "Thread rolling is commonly performed at room temperature. "
    "Some forming variants heat the workpiece before forming."
)


class FakeSearch:
    name = "brave"

    def __init__(self, results=None, error=None):
        self.results = (
            results
            if results is not None
            else [
                SearchResult(
                    url=NIST_URL, title="NIST thread rolling", snippet="Technical", rank=1
                ),
                SearchResult(url=ASME_URL, title="ASME forming", snippet="Technical", rank=2),
            ]
        )
        self.error = error
        self.calls = 0

    def search(self, query, count):
        self.calls += 1
        if self.error:
            raise self.error
        return self.results[:count]


class FakeDocumentFetcher:
    def __init__(self, documents=None):
        self.documents = documents or {
            NIST_URL: FetchedDocument(NIST_TEXT, "NIST thread rolling"),
            ASME_URL: FetchedDocument(ASME_TEXT, "ASME forming"),
        }
        self.calls = 0

    def fetch(self, url):
        self.calls += 1
        return self.documents[url]


def extraction_payload(excerpt="Thread rolling forms threads through plastic deformation."):
    return {
        "alternate_process_hypotheses": ["thread rolling", "thread cutting"],
        "evidence": [
            {
                "evidence_id": "e1",
                "source_url": NIST_URL,
                "excerpt": excerpt,
                "location": "main text",
                "normalized_fact": "Thread rolling forms threads by plastic deformation.",
                "category": "process",
                "source_says_confidence": 0.98,
            },
            {
                "evidence_id": "e2",
                "source_url": ASME_URL,
                "excerpt": "Thread rolling is commonly performed at room temperature.",
                "location": "main text",
                "normalized_fact": "Thread rolling may occur at room temperature.",
                "category": "conditions",
                "source_says_confidence": 0.9,
            },
            {
                "evidence_id": "e3",
                "source_url": ASME_URL,
                "excerpt": "Some forming variants heat the workpiece before forming.",
                "location": "main text",
                "normalized_fact": "Some forming variants use heat.",
                "category": "conditions",
                "source_says_confidence": 0.9,
            },
            {
                "evidence_id": "e5",
                "source_url": ASME_URL,
                "excerpt": "Rolling dies form the thread without cutting.",
                "location": "main text",
                "normalized_fact": "Rolling dies form a thread without cutting.",
                "category": "process",
                "source_says_confidence": 0.9,
            },
            {
                "evidence_id": "e4",
                "source_url": NIST_URL,
                "excerpt": "A documented machine example produces 100 parts per minute.",
                "location": "main text",
                "normalized_fact": "One documented machine produces 100 parts per minute.",
                "category": "quantitative",
                "source_says_confidence": 0.95,
            },
        ],
        "candidate_claims": [
            {
                "claim_key": "c1",
                "statement": "Thread rolling forms threads through plastic deformation.",
                "category": "process",
                "evidence_ids": ["e1", "e5"],
                "video_applicability_confidence": 0.55,
                "notes": "Process fact is stronger than video identification.",
            },
            {
                "claim_key": "c2",
                "statement": "A documented machine produces 100 parts per minute.",
                "category": "quantitative",
                "evidence_ids": ["e4"],
                "video_applicability_confidence": 0.1,
                "notes": "Not necessarily the machine in the video.",
            },
            {
                "claim_key": "c3",
                "statement": "The video machine uses a specific alloy.",
                "category": "material",
                "evidence_ids": [],
                "video_applicability_confidence": 0.05,
                "notes": "Unsupported.",
            },
            {
                "claim_key": "c4",
                "statement": "The process always occurs at room temperature.",
                "category": "conditions",
                "evidence_ids": ["e2", "e3"],
                "video_applicability_confidence": 0.2,
                "notes": "Sources describe variants.",
            },
        ],
    }


def synthesis_payload():
    return {
        "research_summary": "Sources describe thread rolling, with unresolved video applicability.",
        "verified_process_name": "thread rolling",
        "process_confidence": 0.55,
        "material": None,
        "machine_types": [],
        "process_steps": [
            {"statement": "Rolling dies form the thread without cutting.", "claim_keys": ["c1"]}
        ],
        "technical_explanations": [
            {
                "statement": "Threads may be formed through plastic deformation.",
                "claim_keys": ["c1"],
            }
        ],
        "interesting_facts": [
            {
                "statement": "A documented machine produces 100 parts per minute.",
                "claim_keys": ["c2"],
            }
        ],
        "safety_notes": [],
        "unknowns": ["The machine and alloy in this video remain unverified."],
        "claims": [
            {
                "claim_key": "c1",
                "status": "VERIFIED",
                "source_support_confidence": 0.95,
                "video_applicability_confidence": 0.55,
                "supporting_evidence_ids": ["e1", "e5"],
                "contradicting_evidence_ids": [],
                "notes": "Multiple sources support the general process fact.",
            },
            {
                "claim_key": "c2",
                "status": "VERIFIED",
                "source_support_confidence": 0.95,
                "video_applicability_confidence": 0.1,
                "supporting_evidence_ids": ["e4"],
                "contradicting_evidence_ids": [],
                "notes": "Applies only to the documented machine.",
            },
            {
                "claim_key": "c3",
                "status": "UNVERIFIED",
                "source_support_confidence": 0,
                "video_applicability_confidence": 0.05,
                "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [],
                "notes": "No evidence.",
            },
            {
                "claim_key": "c4",
                "status": "CONTRADICTED",
                "source_support_confidence": 0.7,
                "video_applicability_confidence": 0.2,
                "supporting_evidence_ids": ["e2"],
                "contradicting_evidence_ids": ["e3"],
                "notes": "The absolute wording fails across variants.",
            },
        ],
        "contradictions": [
            {
                "claim_keys": ["c4"],
                "evidence_ids": ["e2", "e3"],
                "description": "Sources describe cold and heated variants.",
                "possible_explanations": ["Different forming variants."],
            }
        ],
    }


class FakeResearchAI:
    name = "gemini"
    model = "test-research-model"
    prompt_version = "technical-research-v1"

    def __init__(self, extraction=None, synthesis=None, error=None):
        self.extraction = EvidenceExtractionPayload.model_validate(
            extraction or extraction_payload()
        )
        self.synthesis = ResearchSynthesisPayload.model_validate(synthesis or synthesis_payload())
        self.error = error
        self.extract_calls = 0
        self.verify_calls = 0

    def extract_evidence(self, analysis, questions, documents):
        self.extract_calls += 1
        if self.error:
            raise self.error
        return self.extraction

    def verify_claims(self, analysis, extraction, documents):
        self.verify_calls += 1
        if self.error:
            raise self.error
        return self.synthesis

    def close(self):
        pass


def seed_research_candidate(session, video):
    video = NormalizedVideo.model_validate(video.model_dump(exclude={"orientation"}))
    repository = CandidateRepository(session)
    candidate, _ = repository.add_if_new(video, "screw", Idea(object_name="screw"))
    payload = VideoVisualAnalysisPayload.model_validate(analysis_payload())
    analysis = VideoVisualAnalysis(
        **payload.model_dump(),
        provider="gemini",
        model="visual-model",
        prompt_version="visual-analysis-v1",
        analysis_version="1.0",
    )
    inputs = ScoreInputs(**dict.fromkeys(ScoreInputs.model_fields, 80))
    score = ScoreResult(
        method="ai_visual",
        version="2.0",
        inputs=inputs,
        weights=ScoringWeights().model_dump(),
        total=80,
        rationale=dict.fromkeys(ScoreInputs.model_fields, "Visual evidence."),
        analyzed_video=True,
    )
    apply_score(candidate, score)
    candidate.status = CandidateStatus.SCORED
    repository.add_evaluation(candidate, score, analysis=analysis, overall_confidence=0.9)
    session.flush()
    return candidate


@pytest.fixture
def research_candidate(session, video):
    return seed_research_candidate(session, video)


def make_service(session, search=None, fetcher=None, ai=None, *, min_sources=2):
    search = search or FakeSearch()
    fetcher = fetcher or FakeDocumentFetcher()
    ai = ai or FakeResearchAI()
    service = TechnicalResearchService(
        CandidateRepository(session),
        ResearchRepository(session),
        search,
        fetcher,
        ai,
        ResearchPlanner(5),
        SourceRanker(),
        max_sources=8,
        max_pages=5,
        min_sources=min_sources,
        max_llm_calls=2,
    )
    return service, search, fetcher, ai


def test_planner_separates_general_and_claim_verification(research_candidate, session):
    evaluation = CandidateRepository(session).latest_by_method(research_candidate.id, "ai_visual")
    analysis = VideoVisualAnalysis.model_validate(evaluation.analysis)
    questions, queries = ResearchPlanner(3).build(analysis, "screw")
    assert len(queries) == 3
    assert {item.kind for item in questions} == {"GENERAL_PROCESS", "CLAIM_VERIFICATION"}
    assert any("screw" in item.query for item in queries)


def test_source_ranking_prioritizes_authoritative_and_demotes_social():
    results = [
        SearchResult(url="https://reddit.com/r/test", title="Forum", rank=1),
        SearchResult(url="https://nist.gov/process", title="NIST", rank=3),
        SearchResult(url="https://example.com/article", title="Article", rank=2),
    ]
    ranked = SourceRanker().rank(results)
    assert [item[1] for item in ranked] == [
        SourceTier.TIER_A,
        SourceTier.TIER_C,
        SourceTier.TIER_D,
    ]


def test_research_pipeline_persists_claim_source_graph_and_contradictions(
    session, research_candidate
):
    service, search, fetcher, ai = make_service(session)
    response = service.research(research_candidate.id)
    dossier = response.dossier
    assert response.reused is False
    assert len(dossier.sources) == 2
    assert len(dossier.claims) == 4
    assert dossier.claims[0].status == ClaimStatus.VERIFIED
    assert len(dossier.claims[0].evidence) == 2
    assert dossier.claims[1].quantitative is True
    assert dossier.claims[2].status == ClaimStatus.UNVERIFIED
    assert dossier.claims[3].status == ClaimStatus.CONTRADICTED
    assert dossier.contradictions[0].resolved is False
    assert dossier.visual_observations
    assert search.calls == 5 and fetcher.calls == 2
    assert ai.extract_calls == ai.verify_calls == 1


def test_research_cache_force_history_and_human_review(session, research_candidate):
    service, search, fetcher, ai = make_service(session)
    first = service.research(research_candidate.id)
    reused = service.research(research_candidate.id)
    forced = service.research(research_candidate.id, force=True)
    assert reused.reused is True
    assert reused.dossier.id == first.dossier.id
    assert forced.dossier.id != first.dossier.id
    assert search.calls == 10
    assert fetcher.calls == 4
    assert ai.extract_calls == ai.verify_calls == 2
    review = ResearchReviewService(
        CandidateRepository(session), ResearchRepository(session)
    ).review(
        research_candidate.id,
        ResearchReview(decision="VERIFIED", reviewer="Human", notes="Sources checked"),
    )
    assert review.status == "VERIFIED"
    assert len(ResearchRepository(session).list(research_candidate.id)) == 2


def test_invalid_excerpt_does_not_persist(session, research_candidate):
    ai = FakeResearchAI(extraction=extraction_payload(excerpt="Fabricated quote"))
    service, _, _, _ = make_service(session, ai=ai)
    with pytest.raises(ResearchValidationError, match="not present"):
        service.research(research_candidate.id)
    assert ResearchRepository(session).list(research_candidate.id) == []


def test_no_results_and_missing_visual_analysis_are_distinct(session, video, research_candidate):
    service, _, _, _ = make_service(session, search=FakeSearch(results=[]))
    with pytest.raises(InsufficientEvidenceError, match="no useful"):
        service.research(research_candidate.id)
    other_video = NormalizedVideo.model_validate(
        video.model_dump(exclude={"orientation"}) | {"provider_video_id": "no-ai"}
    )
    other, _ = CandidateRepository(session).add_if_new(
        other_video, "other", Idea(object_name="part")
    )
    session.flush()
    with pytest.raises(ConflictError, match="AI visual analysis"):
        service.research(other.id)


def test_search_provider_failure_is_not_persisted(session, research_candidate):
    service, _, _, _ = make_service(
        session, search=FakeSearch(error=SearchProviderError("search failed"))
    )
    with pytest.raises(SearchProviderError, match="search failed"):
        service.research(research_candidate.id)
    assert ResearchRepository(session).list(research_candidate.id) == []


def test_quantitative_claim_needs_high_quality_source(session):
    service, _, _, _ = make_service(session)
    extraction = EvidenceExtractionPayload.model_validate(extraction_payload())
    synthesis = ResearchSynthesisPayload.model_validate(synthesis_payload())
    decision = next(item for item in synthesis.claims if item.claim_key == "c2")
    evidence = {item.evidence_id: item for item in extraction.evidence}
    sources = {NIST_URL: SimpleNamespace(tier=SourceTier.TIER_C)}
    assert (
        service._policy_status(
            decision, "A machine produces 100 parts per minute.", evidence, sources
        )
        == ClaimStatus.PARTIALLY_SUPPORTED
    )


def test_structured_claim_rules_reject_false_verification():
    with pytest.raises(ValidationError, match="supporting evidence"):
        ClaimVerificationDecision(
            claim_key="c1",
            status="VERIFIED",
            source_support_confidence=0.9,
            video_applicability_confidence=0.5,
        )
