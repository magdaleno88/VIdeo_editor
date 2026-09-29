from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator

NonEmpty = Annotated[str, Field(min_length=1, max_length=200)]
Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)


class ProviderName(StrEnum):
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    LONG_FORM = "long_form"


class CandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    SCORED = "SCORED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    PROCESSING = "PROCESSING"
    READY = "READY"


class RightsStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    RESTRICTED = "RESTRICTED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class SourceLicensePreset(StrEnum):
    CC0 = "CC0"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    CC_BY = "CC_BY"
    CC_BY_SA = "CC_BY_SA"
    CUSTOM = "CUSTOM"


class Orientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"
    UNKNOWN = "unknown"


class RightsInfo(Contract):
    source: HttpUrl
    creator: str | None = None
    license_name: str | None = None
    license_url: HttpUrl | None = None
    commercial_use_allowed: bool | None = None
    modification_allowed: bool | None = None
    attribution_required: bool | None = None
    attribution_text: str | None = None
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    verification_date: datetime | None = None
    verified_by: str | None = None
    evidence_url: HttpUrl | None = None
    notes: str = ""


class NormalizedVideo(Contract):
    provider: ProviderName
    provider_video_id: NonEmpty
    source_url: HttpUrl
    preview_url: HttpUrl | None = None
    thumbnail_url: HttpUrl | None = None
    title: NonEmpty
    description: str = ""
    duration: float | None = Field(None, ge=0, allow_inf_nan=False)
    width: int | None = Field(None, gt=0)
    height: int | None = Field(None, gt=0)
    author: str | None = None
    author_url: HttpUrl | None = None
    rights: RightsInfo

    @computed_field
    @property
    def orientation(self) -> Orientation:
        if self.width is None or self.height is None:
            return Orientation.UNKNOWN
        if self.width == self.height:
            return Orientation.SQUARE
        return Orientation.PORTRAIT if self.height > self.width else Orientation.LANDSCAPE


class Idea(Contract):
    object_name: Annotated[str, Field(min_length=1, max_length=60)]
    process_name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    category: Annotated[str, Field(min_length=1, max_length=60)] | None = None


class DiscoveryRequest(Idea):
    providers: list[ProviderName] | None = Field(None, min_length=1, max_length=2)
    max_queries: int = Field(3, ge=1, le=8)
    page: int = Field(1, ge=1, le=10)
    per_page: int = Field(10, ge=3, le=40)


class ScoreInputs(Contract):
    movement: Score
    visible_transformation: Score
    satisfying_result: Score
    unusual_machinery: Score
    understandable_without_audio: Score
    visual_hook: Score
    educational_potential: Score
    loop_potential: Score


class ScoreResult(Contract):
    method: Literal["metadata_heuristic", "manual", "ai_visual", "source_structural"]
    version: str = "1.0"
    inputs: ScoreInputs
    weights: dict[str, float]
    total: Score
    rationale: dict[str, str]
    unknown_dimensions: list[str] = Field(default_factory=list)
    analyzed_video: bool = False


class ReviewAction(Contract):
    reviewer: NonEmpty
    notes: Annotated[str, Field(min_length=1, max_length=4000)]


class ManualScoreRequest(ReviewAction):
    inputs: ScoreInputs


class RightsReview(ReviewAction):
    rights_status: RightsStatus
    license_name: NonEmpty | None = None
    license_url: HttpUrl | None = None
    commercial_use_allowed: bool | None = None
    modification_allowed: bool | None = None
    attribution_required: bool | None = None
    attribution_text: Annotated[str, Field(max_length=4000)] | None = None
    evidence_url: HttpUrl | None = None

    @model_validator(mode="after")
    def require_evidence(self) -> "RightsReview":
        if self.rights_status == RightsStatus.VERIFIED:
            if not (self.license_name and self.license_url and self.evidence_url):
                raise ValueError("Verified rights require a license, license URL and evidence URL")
            if self.commercial_use_allowed is not True or self.modification_allowed is not True:
                raise ValueError(
                    "Verified rights require commercial use and modification permission"
                )
            if self.attribution_required is None:
                raise ValueError("Verified rights require an explicit attribution decision")
            if self.attribution_required and not self.attribution_text:
                raise ValueError("Required attribution text is missing")
        return self


class CandidateFilters(Contract):
    provider: ProviderName | None = None
    status: CandidateStatus | None = None
    minimum_score: Score | None = None
    category: NonEmpty | None = None
    industrial_process: NonEmpty | None = None
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class CandidateRead(Contract):
    id: int
    provider: ProviderName
    provider_video_id: str
    source_url: str
    preview_url: str | None
    thumbnail_url: str | None
    title: str
    description: str
    duration: float | None
    width: int | None
    height: int | None
    orientation: Orientation
    author: str | None
    author_url: str | None
    license_name: str | None
    license_url: str | None
    commercial_use_allowed: bool | None
    attribution_required: bool | None
    rights_status: RightsStatus
    rights: RightsInfo
    search_query: str
    category: str | None
    industrial_process: str | None
    object_being_manufactured: str
    visual_score: float | None
    transformation_score: float | None
    machine_score: float | None
    hook_score: float | None
    loop_score: float | None
    educational_score: float | None
    total_score: float | None
    score_details: ScoreResult | None
    status: CandidateStatus
    created_at: datetime
    updated_at: datetime


class CandidatePage(Contract):
    items: list[CandidateRead]
    total: int
    limit: int
    offset: int


class DiscoveryIssue(Contract):
    provider: ProviderName
    query: str
    message: str


class DiscoveryResult(Contract):
    queries: list[str]
    candidate_ids: list[int]
    created: int
    duplicates: int
    cache_hits: int
    successful_searches: int
    errors: list[DiscoveryIssue]
    warnings: list[DiscoveryIssue]


class EventRead(Contract):
    id: int
    action: str
    reviewer: str
    notes: str
    details: dict[str, Any]
    created_at: datetime


class ScoreDimension(StrEnum):
    MOVEMENT = "movement"
    VISIBLE_TRANSFORMATION = "visible_transformation"
    SATISFYING_RESULT = "satisfying_result"
    UNUSUAL_MACHINERY = "unusual_machinery"
    UNDERSTANDABLE_WITHOUT_AUDIO = "understandable_without_audio"
    VISUAL_HOOK = "visual_hook"
    EDUCATIONAL_POTENTIAL = "educational_potential"
    LOOP_POTENTIAL = "loop_potential"


class VisualEvidence(Contract):
    timestamp_start: float = Field(ge=0)
    timestamp_end: float = Field(ge=0)
    frame_timestamp: float | None = Field(None, ge=0)
    observation: Annotated[str, Field(min_length=1, max_length=1000)]
    related_dimension: ScoreDimension
    confidence: Confidence

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> "VisualEvidence":
        if self.timestamp_end < self.timestamp_start:
            raise ValueError("Evidence end timestamp must be at or after its start")
        if self.frame_timestamp is not None and not (
            self.timestamp_start <= self.frame_timestamp <= self.timestamp_end
        ):
            raise ValueError("Frame timestamp must fall inside the evidence interval")
        return self


class DimensionAnalysis(Contract):
    rating: Score
    confidence: Confidence
    explanation: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: list[VisualEvidence] = Field(default_factory=list, max_length=20)


class VisualDimensions(Contract):
    movement: DimensionAnalysis
    visible_transformation: DimensionAnalysis
    satisfying_result: DimensionAnalysis
    unusual_machinery: DimensionAnalysis
    understandable_without_audio: DimensionAnalysis
    visual_hook: DimensionAnalysis
    educational_potential: DimensionAnalysis
    loop_potential: DimensionAnalysis

    @model_validator(mode="after")
    def evidence_matches_dimension(self) -> "VisualDimensions":
        for name, assessment in self:
            if any(item.related_dimension.value != name for item in assessment.evidence):
                raise ValueError(f"Evidence dimension mismatch in {name}")
        return self


class VisualSegment(Contract):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    confidence: Confidence

    @model_validator(mode="after")
    def segment_is_ordered(self) -> "VisualSegment":
        if self.end <= self.start:
            raise ValueError("Segment end must be after its start")
        return self


class FirstSecondsAnalysis(Contract):
    duration_observed: float = Field(gt=0, le=3)
    immediate_motion: str = Field(min_length=1, max_length=500)
    visual_novelty: str = Field(min_length=1, max_length=500)
    visible_transformation: str = Field(min_length=1, max_length=500)
    visual_clarity: str = Field(min_length=1, max_length=500)
    context_required: str = Field(min_length=1, max_length=500)
    confidence: Confidence


class VideoVisualAnalysisPayload(Contract):
    summary: Annotated[str, Field(min_length=1, max_length=3000)]
    detected_process: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    detected_object: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    duration_analyzed: float = Field(gt=0)
    dimensions: VisualDimensions
    evidence: list[VisualEvidence] = Field(default_factory=list, max_length=100)
    notable_moments: list[VisualSegment] = Field(default_factory=list, max_length=10)
    potential_hook_segments: list[VisualSegment] = Field(default_factory=list, max_length=5)
    potential_loop_segments: list[VisualSegment] = Field(default_factory=list, max_length=5)
    first_seconds: FirstSecondsAnalysis
    warnings: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def timestamps_within_duration(self) -> "VideoVisualAnalysisPayload":
        intervals = [
            *self.evidence,
            *self.notable_moments,
            *self.potential_hook_segments,
            *self.potential_loop_segments,
        ]
        intervals += [item for _, dimension in self.dimensions for item in dimension.evidence]
        if any(
            (item.end if isinstance(item, VisualSegment) else item.timestamp_end)
            > self.duration_analyzed
            for item in intervals
        ):
            raise ValueError("Evidence and segment timestamps must be within analyzed duration")
        if self.first_seconds.duration_observed > min(3, self.duration_analyzed):
            raise ValueError("First-seconds observation exceeds analyzed duration")
        return self


class VideoVisualAnalysis(VideoVisualAnalysisPayload):
    provider: Literal["gemini", "long_form_local"]
    model: NonEmpty
    analysis_version: NonEmpty
    prompt_version: NonEmpty


class EvaluationRead(Contract):
    id: int
    candidate_id: int
    method: Literal["metadata_heuristic", "manual", "ai_visual", "source_structural"]
    scorer_version: str
    ai_provider: str | None
    ai_model: str | None
    prompt_version: str | None
    weights: dict[str, float]
    ratings: ScoreInputs
    total_score: Score
    overall_confidence: Confidence | None
    analysis: VideoVisualAnalysis | None
    created_at: datetime


class AIScoreRequest(Contract):
    force: bool = False


class AIScoreResponse(Contract):
    evaluation: EvaluationRead
    reused: bool


class ScoreComparison(Contract):
    candidate_id: int
    ai_evaluation_id: int
    manual_evaluation_id: int
    ai_score: Score
    manual_score: Score
    total_difference: float
    differences_by_dimension: dict[ScoreDimension, float]
    mean_absolute_error: float = Field(ge=0, le=100)


class KnowledgeType(StrEnum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    VERIFIED_FACT = "VERIFIED_FACT"
    UNKNOWN = "UNKNOWN"


class ResearchStatus(StrEnum):
    NEEDS_REVIEW = "NEEDS_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ClaimStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"


class SourceTier(StrEnum):
    TIER_A = "TIER_A"
    TIER_B = "TIER_B"
    TIER_C = "TIER_C"
    TIER_D = "TIER_D"


class SourceType(StrEnum):
    STANDARD = "STANDARD"
    GOVERNMENT = "GOVERNMENT"
    ACADEMIC = "ACADEMIC"
    MANUFACTURER = "MANUFACTURER"
    INDUSTRY_ASSOCIATION = "INDUSTRY_ASSOCIATION"
    TECHNICAL_MEDIA = "TECHNICAL_MEDIA"
    EDUCATIONAL = "EDUCATIONAL"
    FORUM_SOCIAL = "FORUM_SOCIAL"
    OTHER = "OTHER"


class EvidenceRelation(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    CONTEXT = "CONTEXT"


class ResearchQuestion(Contract):
    kind: Literal["GENERAL_PROCESS", "CLAIM_VERIFICATION"]
    question: Annotated[str, Field(min_length=1, max_length=600)]


class ResearchQuery(Contract):
    kind: Literal["GENERAL_PROCESS", "CLAIM_VERIFICATION"]
    query: Annotated[str, Field(min_length=1, max_length=600)]


class SearchResult(Contract):
    url: HttpUrl
    title: Annotated[str, Field(min_length=1, max_length=500)]
    snippet: Annotated[str, Field(max_length=3000)] = ""
    rank: int = Field(ge=1)


class ResearchDocument(Contract):
    url: HttpUrl
    title: Annotated[str, Field(min_length=1, max_length=500)]
    publisher: Annotated[str, Field(min_length=1, max_length=300)]
    author: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    publication_date: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    content: Annotated[str, Field(min_length=1, max_length=20000)]
    search_snippet: Annotated[str, Field(max_length=3000)] = ""
    source_type: SourceType
    tier: SourceTier
    relevance: Confidence
    credibility: Confidence


class ExtractedEvidence(Contract):
    evidence_id: NonEmpty
    source_url: HttpUrl
    excerpt: Annotated[str, Field(min_length=1, max_length=2000)]
    location: Annotated[str, Field(min_length=1, max_length=500)]
    normalized_fact: Annotated[str, Field(min_length=1, max_length=2000)]
    category: Annotated[str, Field(min_length=1, max_length=100)]
    source_says_confidence: Confidence


class CandidateResearchClaim(Contract):
    claim_key: NonEmpty
    statement: Annotated[str, Field(min_length=1, max_length=2000)]
    category: Annotated[str, Field(min_length=1, max_length=100)]
    evidence_ids: list[NonEmpty] = Field(default_factory=list, max_length=20)
    video_applicability_confidence: Confidence
    notes: Annotated[str, Field(max_length=2000)] = ""


class EvidenceExtractionPayload(Contract):
    alternate_process_hypotheses: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=10
    )
    evidence: list[ExtractedEvidence] = Field(min_length=1, max_length=100)
    candidate_claims: list[CandidateResearchClaim] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def references_existing_evidence(self) -> "EvidenceExtractionPayload":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence IDs must be unique")
        known = set(evidence_ids)
        claim_keys = [item.claim_key for item in self.candidate_claims]
        if len(claim_keys) != len(set(claim_keys)):
            raise ValueError("Claim keys must be unique")
        if any(set(claim.evidence_ids) - known for claim in self.candidate_claims):
            raise ValueError("Candidate claim references unknown evidence")
        return self


class ClaimVerificationDecision(Contract):
    claim_key: NonEmpty
    status: ClaimStatus
    source_support_confidence: Confidence
    video_applicability_confidence: Confidence
    supporting_evidence_ids: list[NonEmpty] = Field(default_factory=list, max_length=20)
    contradicting_evidence_ids: list[NonEmpty] = Field(default_factory=list, max_length=20)
    notes: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def status_has_required_evidence(self) -> "ClaimVerificationDecision":
        if (
            self.status
            in (
                ClaimStatus.VERIFIED,
                ClaimStatus.PARTIALLY_SUPPORTED,
            )
            and not self.supporting_evidence_ids
        ):
            raise ValueError("Supported claims require supporting evidence")
        if self.status == ClaimStatus.CONTRADICTED and not self.contradicting_evidence_ids:
            raise ValueError("Contradicted claims require contradicting evidence")
        if self.status == ClaimStatus.VERIFIED and self.contradicting_evidence_ids:
            raise ValueError("A verified claim cannot retain contradictory evidence")
        return self


class ResearchContradictionPayload(Contract):
    claim_keys: list[NonEmpty] = Field(min_length=1, max_length=10)
    evidence_ids: list[NonEmpty] = Field(min_length=2, max_length=20)
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    possible_explanations: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=10
    )


class SourcedResearchItem(Contract):
    statement: Annotated[str, Field(min_length=1, max_length=1500)]
    claim_keys: list[NonEmpty] = Field(min_length=1, max_length=20)


class ResearchSynthesisPayload(Contract):
    research_summary: Annotated[str, Field(min_length=1, max_length=5000)]
    verified_process_name: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    process_confidence: Confidence
    material: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    machine_types: list[SourcedResearchItem] = Field(default_factory=list, max_length=20)
    process_steps: list[SourcedResearchItem] = Field(default_factory=list, max_length=30)
    technical_explanations: list[SourcedResearchItem] = Field(default_factory=list, max_length=30)
    interesting_facts: list[SourcedResearchItem] = Field(default_factory=list, max_length=30)
    safety_notes: list[SourcedResearchItem] = Field(default_factory=list, max_length=20)
    unknowns: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=30
    )
    claims: list[ClaimVerificationDecision] = Field(min_length=1, max_length=50)
    contradictions: list[ResearchContradictionPayload] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def synthesis_references_decided_claims(self) -> "ResearchSynthesisPayload":
        claim_keys = {item.claim_key for item in self.claims}
        sourced_items = [
            *self.machine_types,
            *self.process_steps,
            *self.technical_explanations,
            *self.interesting_facts,
            *self.safety_notes,
        ]
        if any(set(item.claim_keys) - claim_keys for item in sourced_items):
            raise ValueError("Synthesis item references an unknown claim")
        return self


class ResearchRequest(Contract):
    force: bool = False


class ResearchReview(ReviewAction):
    decision: Literal["VERIFIED", "REJECTED"]


class ResearchSourceRead(Contract):
    id: int
    url: str
    title: str
    publisher: str
    author: str | None
    publication_date: str | None
    accessed_at: datetime
    source_type: SourceType
    tier: SourceTier
    relevance: Confidence
    credibility: Confidence
    search_snippet: str
    notes: str


class ClaimEvidenceRead(Contract):
    id: int
    source_id: int
    source_url: str
    relation: EvidenceRelation
    excerpt: str
    location: str
    source_says_confidence: Confidence


class ResearchClaimRead(Contract):
    id: int
    statement: str
    category: str
    status: ClaimStatus
    knowledge_type: KnowledgeType
    source_support_confidence: Confidence
    video_applicability_confidence: Confidence
    quantitative: bool
    notes: str
    evidence: list[ClaimEvidenceRead]


class ResearchContradictionRead(Contract):
    id: int
    claim_ids: list[int]
    source_ids: list[int]
    description: str
    possible_explanations: list[str]
    resolved: bool
    notes: str


class ResearchDossierRead(Contract):
    id: int
    candidate_id: int
    status: ResearchStatus
    detected_object: str | None
    detected_process: str | None
    visual_observations: list[dict[str, Any]]
    alternate_process_hypotheses: list[str]
    research_questions: list[ResearchQuestion]
    search_queries: list[ResearchQuery]
    research_summary: str
    verified_process_name: str | None
    process_confidence: Confidence
    material: str | None
    machine_types: list[SourcedResearchItem]
    process_steps: list[SourcedResearchItem]
    technical_explanations: list[SourcedResearchItem]
    interesting_facts: list[SourcedResearchItem]
    safety_notes: list[SourcedResearchItem]
    unknowns: list[str]
    search_provider: str
    research_provider: str
    model: str
    prompt_version: str
    research_version: str
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str
    sources: list[ResearchSourceRead]
    claims: list[ResearchClaimRead]
    contradictions: list[ResearchContradictionRead]


class ResearchRunResponse(Contract):
    dossier: ResearchDossierRead
    reused: bool


class ScriptStyle(StrEnum):
    EDUCATIONAL = "EDUCATIONAL"
    CURIOSITY = "CURIOSITY"
    PROCESS_EXPLAINER = "PROCESS_EXPLAINER"


class TargetPlatform(StrEnum):
    FACEBOOK_REELS = "FACEBOOK_REELS"


class ScriptStatus(StrEnum):
    VALIDATED = "VALIDATED"
    NEEDS_REVISION = "NEEDS_REVISION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BeatKind(StrEnum):
    HOOK = "HOOK"
    SETUP = "SETUP"
    TRANSFORMATION = "TRANSFORMATION"
    TECHNICAL_INSIGHT = "TECHNICAL_INSIGHT"
    PAYOFF = "PAYOFF"


class ScriptFactualityType(StrEnum):
    OBSERVATION = "OBSERVATION"
    VERIFIED_FACT = "VERIFIED_FACT"
    INFERENCE = "INFERENCE"
    EDITORIAL = "EDITORIAL"


class PermittedScriptClaim(Contract):
    id: int
    statement: str
    status: ClaimStatus
    quantitative: bool
    caution_required: bool = False


class PermittedVisualEvidence(Contract):
    ref: NonEmpty
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    kind: Literal["EVIDENCE", "HOOK", "NOTABLE", "LOOP"]


class ScriptSentencePayload(Contract):
    display_text: Annotated[str, Field(min_length=1, max_length=500)]
    spoken_text: Annotated[str, Field(min_length=1, max_length=500)]
    factuality_type: ScriptFactualityType
    claim_ids: list[int] = Field(default_factory=list, max_length=10)
    visual_refs: list[NonEmpty] = Field(default_factory=list, max_length=10)


class ScriptBeatPayload(Contract):
    kind: BeatKind
    recommended_start: float = Field(ge=0)
    recommended_end: float = Field(gt=0)
    visual_direction: Annotated[str, Field(min_length=1, max_length=1000)]
    sentences: list[ScriptSentencePayload] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def interval_is_ordered(self) -> "ScriptBeatPayload":
        if self.recommended_end <= self.recommended_start:
            raise ValueError("Beat end must be after its start")
        return self


class ScriptGenerationPayload(Contract):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    hook: Annotated[str, Field(min_length=1, max_length=500)]
    beats: list[ScriptBeatPayload] = Field(min_length=2, max_length=12)
    closing: Annotated[str, Field(min_length=1, max_length=500)]
    warnings: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def begins_with_hook(self) -> "ScriptGenerationPayload":
        if self.beats[0].kind != BeatKind.HOOK:
            raise ValueError("The first script beat must be HOOK")
        return self


class ScriptSentenceValidation(Contract):
    sentence_index: int = Field(ge=0)
    supported: bool
    notes: Annotated[str, Field(max_length=500)] = ""


class ScriptValidationPayload(Contract):
    sentences: list[ScriptSentenceValidation] = Field(min_length=1, max_length=100)
    external_facts_detected: bool = False
    clickbait_detected: bool = False
    notes: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )


class ScriptGenerationRequest(Contract):
    style: ScriptStyle = ScriptStyle.EDUCATIONAL
    target_platform: TargetPlatform = TargetPlatform.FACEBOOK_REELS
    target_duration_seconds: Literal[30, 45, 60] = 45
    language: Literal["es"] = "es"
    variants: int = Field(1, ge=1, le=3)
    force: bool = False
    editorial_context: Annotated[str, Field(max_length=2000)] = ""


class ScriptReviewRequest(ReviewAction):
    decision: Literal["APPROVED", "REJECTED"]


class ScriptSentenceRead(Contract):
    id: int
    position: int
    display_text: str
    spoken_text: str
    factuality_type: ScriptFactualityType
    claim_ids: list[int]
    visual_refs: list[str]


class ScriptBeatRead(Contract):
    id: int
    position: int
    kind: BeatKind
    recommended_start: float
    recommended_end: float
    visual_direction: str
    sentences: list[ScriptSentenceRead]


class ScriptReviewRead(Contract):
    id: int
    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str
    notes: str
    created_at: datetime


class ScriptDraftRead(Contract):
    id: int
    candidate_id: int
    research_dossier_id: int
    visual_evaluation_id: int
    status: ScriptStatus
    style: ScriptStyle
    target_platform: TargetPlatform
    language: str
    target_duration_seconds: int
    estimated_duration_seconds: float
    word_count: int
    speaking_rate: float
    title: str
    hook: str
    closing: str
    full_narration: str
    warnings: list[str]
    provider: str
    model: str
    prompt_version: str
    script_version: str
    variant_index: int
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str
    beats: list[ScriptBeatRead]
    reviews: list[ScriptReviewRead]


class ScriptGenerationResponse(Contract):
    scripts: list[ScriptDraftRead]
    reused: bool


class NarrationStatus(StrEnum):
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class AlignmentMethod(StrEnum):
    PROVIDER_ALIGNMENT = "PROVIDER_ALIGNMENT"
    ESTIMATED_ALIGNMENT = "ESTIMATED_ALIGNMENT"


class DurationStatus(StrEnum):
    WITHIN_TARGET = "WITHIN_TARGET"
    TOO_SHORT = "TOO_SHORT"
    TOO_LONG = "TOO_LONG"


class VoiceSettings(Contract):
    stability: float | None = Field(None, ge=0, le=1)
    similarity_boost: float | None = Field(None, ge=0, le=1)
    style: float | None = Field(None, ge=0, le=1)
    speed: float | None = Field(None, ge=0.7, le=1.2)
    use_speaker_boost: bool | None = None

    def supported_values(self) -> dict[str, float | bool]:
        return self.model_dump(exclude_none=True)


class NarrationRequest(Contract):
    provider: Literal["elevenlabs"] = "elevenlabs"
    voice_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    voice_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    settings: VoiceSettings = Field(default_factory=VoiceSettings)
    force: bool = False


class NarrationReviewRequest(ReviewAction):
    decision: Literal["APPROVED", "REJECTED"]


class VoiceProfileRead(Contract):
    id: int
    provider: str
    provider_voice_id: str
    name: str
    language: str
    description: str
    model: str
    settings: dict[str, Any]
    enabled: bool
    created_at: datetime


class NarrationAlignmentRead(Contract):
    sentence_id: int
    beat_id: int
    start_seconds: float
    end_seconds: float
    method: AlignmentMethod
    confidence: float | None
    word_timings: list[dict[str, Any]] = Field(default_factory=list)


class NarrationBeatTiming(Contract):
    beat_id: int
    start_seconds: float
    end_seconds: float
    method: AlignmentMethod


class NarrationReviewRead(Contract):
    id: int
    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str
    notes: str
    created_at: datetime


class NarrationAssetRead(Contract):
    id: int
    script_id: int
    voice_profile: VoiceProfileRead
    provider: str
    model: str
    voice_settings: dict[str, Any]
    source_text_hash: str
    source_character_count: int
    audio_format: str
    codec: str | None
    sample_rate: int | None
    channels: int | None
    duration_seconds: float
    estimated_duration_seconds: float
    duration_difference_seconds: float
    duration_difference_percent: float
    duration_status: DurationStatus
    file_size_bytes: int
    checksum_sha256: str
    storage_path: str
    generation_version: str
    provider_metadata: dict[str, Any]
    status: NarrationStatus
    alignment_method: AlignmentMethod
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str
    alignments: list[NarrationAlignmentRead]
    beat_timings: list[NarrationBeatTiming]
    reviews: list[NarrationReviewRead]


class NarrationResponse(Contract):
    narration: NarrationAssetRead
    reused: bool


class CompositionStrategy(StrEnum):
    CENTER_CROP = "CENTER_CROP"
    FIT = "FIT"
    BLURRED_BACKGROUND = "BLURRED_BACKGROUND"
    ROI_AWARE = "ROI_AWARE"


class TransitionType(StrEnum):
    CUT = "CUT"


class SourceAudioPolicy(StrEnum):
    MUTED = "MUTED"
    AMBIENT_REDUCED = "AMBIENT_REDUCED"


class RenderStatus(StrEnum):
    RENDERING = "RENDERING"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class RenderPlanRequest(Contract):
    script_id: int = Field(gt=0)
    narration_id: int = Field(gt=0)
    composition: CompositionStrategy = CompositionStrategy.CENTER_CROP
    force: bool = False


class RenderRequest(Contract):
    force: bool = False


class RenderReviewRequest(ReviewAction):
    decision: Literal["APPROVED", "REJECTED"]


class EditSegmentRead(Contract):
    id: int
    position: int
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    playback_speed: float
    composition: CompositionStrategy
    beat_id: int
    sentence_ids: list[int]
    visual_refs: list[str]
    rationale: str
    transition: TransitionType
    loop_count: int
    hold_seconds: float


class VideoEditPlanRead(Contract):
    id: int
    candidate_id: int
    script_id: int
    narration_id: int
    target_platform: TargetPlatform
    aspect_ratio: str
    width: int
    height: int
    fps: float
    target_duration: float
    actual_narration_duration: float
    composition: CompositionStrategy
    source_audio_policy: SourceAudioPolicy
    audio_configuration: dict[str, Any]
    render_configuration: dict[str, Any]
    planner_version: str
    warnings: list[str]
    cache_key: str
    created_at: datetime
    segments: list[EditSegmentRead]


class RenderReviewRead(Contract):
    id: int
    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str
    notes: str
    created_at: datetime


class RenderAssetRead(Contract):
    id: int
    candidate_id: int
    script_id: int
    narration_id: int
    edit_plan_id: int
    output_path: str | None
    width: int | None
    height: int | None
    fps: float | None
    duration: float | None
    planned_duration: float
    duration_difference: float | None
    video_codec: str | None
    audio_codec: str | None
    file_size_bytes: int | None
    checksum_sha256: str | None
    render_version: str
    ffmpeg_version: str | None
    status: RenderStatus
    warnings: list[str]
    cache_key: str
    failure_reason: str
    process_exit_code: int | None
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str
    reviews: list[RenderReviewRead]


class RenderResponse(Contract):
    render: RenderAssetRead
    reused: bool


class CaptionStyleProfile(StrEnum):
    CLEAN = "CLEAN"
    BOLD = "BOLD"
    MINIMAL = "MINIMAL"


class CaptionPosition(StrEnum):
    UPPER = "UPPER"
    CENTER = "CENTER"
    LOWER = "LOWER"


class CaptionTimingMethod(StrEnum):
    PROVIDER_ALIGNMENT = "PROVIDER_ALIGNMENT"
    SENTENCE_ALIGNMENT = "SENTENCE_ALIGNMENT"
    ESTIMATED_ALIGNMENT = "ESTIMATED_ALIGNMENT"


class EmphasisMode(StrEnum):
    NONE = "NONE"
    PHRASE = "PHRASE"
    WORD = "WORD"


class GraphicOverlayType(StrEnum):
    HOOK_TEXT = "HOOK_TEXT"
    INFO_LABEL = "INFO_LABEL"
    BRANDING = "BRANDING"


class FinalRenderStatus(StrEnum):
    RENDERING = "RENDERING"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PilotRunState(StrEnum):
    READY = "READY"
    IN_REVIEW = "IN_REVIEW"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


class QualityRejectionCategory(StrEnum):
    BAD_CROP = "BAD_CROP"
    BAD_CLIP_SELECTION = "BAD_CLIP_SELECTION"
    WEAK_HOOK = "WEAK_HOOK"
    PACING_TOO_SLOW = "PACING_TOO_SLOW"
    PACING_TOO_FAST = "PACING_TOO_FAST"
    CAPTION_PROBLEM = "CAPTION_PROBLEM"
    TTS_PROBLEM = "TTS_PROBLEM"
    VISUAL_NARRATION_MISMATCH = "VISUAL_NARRATION_MISMATCH"
    FACTUAL_PROBLEM = "FACTUAL_PROBLEM"
    AUDIO_SYNC = "AUDIO_SYNC"
    OTHER = "OTHER"


class RevisionStage(StrEnum):
    RESEARCH = "RESEARCH"
    SCRIPT = "SCRIPT"
    NARRATION = "NARRATION"
    RENDER_PLAN = "RENDER_PLAN"
    CAPTION_PLAN = "CAPTION_PLAN"
    FINAL_REVIEW = "FINAL_REVIEW"


class CaptionPlanRequest(Contract):
    style_profile: CaptionStyleProfile | None = None
    position: CaptionPosition = CaptionPosition.LOWER
    emphasis_mode: EmphasisMode = EmphasisMode.PHRASE
    include_hook: bool = True
    factual_claim_ids: list[int] = Field(default_factory=list, max_length=10)
    force: bool = False


class FinalRenderRequest(Contract):
    force: bool = False


class PreviewRequest(Contract):
    time_seconds: float = Field(1.5, ge=0)


class FinalRenderReviewRequest(ReviewAction):
    decision: Literal["APPROVED", "REJECTED"]


class QualityChecklist(Contract):
    first_two_seconds_interesting: bool = False
    visuals_match_narration: bool = False
    important_parts_visible: bool = False
    narration_pacing_natural: bool = False
    captions_readable: bool = False
    captions_preserve_visuals: bool = False
    hook_makes_sense: bool = False
    facts_consistent: bool = False
    cuts_and_loops_natural: bool = False
    audio_synchronized: bool = False
    ready_to_publish: bool = False


class FinalRenderQualityReviewRequest(Contract):
    reviewer: NonEmpty
    decision: Literal["APPROVED", "REJECTED"]
    visual_relevance: int = Field(ge=1, le=5)
    pacing: int = Field(ge=1, le=5)
    crop_quality: int = Field(ge=1, le=5)
    narration_quality: int = Field(ge=1, le=5)
    caption_readability: int = Field(ge=1, le=5)
    hook_strength: int = Field(ge=1, le=5)
    audio_sync: int = Field(ge=1, le=5)
    overall_readiness: int = Field(ge=1, le=5)
    checklist: QualityChecklist
    rejection_categories: list[QualityRejectionCategory] = Field(
        default_factory=list, max_length=10
    )
    notes: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def validate_decision_details(self) -> "FinalRenderQualityReviewRequest":
        if self.decision == "REJECTED" and not self.rejection_categories:
            raise ValueError("Rejected quality reviews require at least one category")
        if self.decision == "APPROVED" and not self.checklist.ready_to_publish:
            raise ValueError("Approved quality reviews must mark the video ready to publish")
        return self


class FinalRenderQualityReviewRead(Contract):
    id: int
    final_render_id: int
    reviewer: str
    decision: Literal["APPROVED", "REJECTED"]
    visual_relevance: int
    pacing: int
    crop_quality: int
    narration_quality: int
    caption_readability: int
    hook_strength: int
    audio_sync: int
    overall_readiness: int
    checklist: QualityChecklist
    rejection_categories: list[QualityRejectionCategory]
    recommended_revision_stage: RevisionStage | None
    notes: str
    created_at: datetime


class PilotBatchCreate(Contract):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]
    description: Annotated[str, Field(max_length=2000)] = ""
    candidate_ids: list[int] = Field(default_factory=list, max_length=20)


class PilotBatchRead(Contract):
    id: int
    name: str
    slug: str
    description: str
    created_at: datetime
    candidate_ids: list[int]


class PilotStageRead(Contract):
    key: str
    label: str
    status: str
    blocked_reason: str | None = None


class PilotProgressRead(Contract):
    batch_id: int
    candidate_id: int
    state: PilotRunState
    next_action: str
    next_action_key: str | None
    blocked_reason: str | None
    stages: list[PilotStageRead]
    external_calls: dict[str, int]
    version_counts: dict[str, int]
    elapsed_seconds: float | None
    technical_warnings: list[str]
    high_risk_flags: list[str]
    technically_ready: bool
    commercial_rights_cleared: bool
    rights_label: str


class LongFormSourceType(StrEnum):
    LOCAL_UPLOAD = "LOCAL_UPLOAD"
    LOCAL_IMPORT = "LOCAL_IMPORT"
    STOCK_PROVIDER = "STOCK_PROVIDER"
    AUTHORIZED_REMOTE = "AUTHORIZED_REMOTE"


class LongFormSourceStatus(StrEnum):
    INGESTED = "INGESTED"
    PREPROCESSED = "PREPROCESSED"
    ANALYZED = "ANALYZED"
    AI_ANALYSIS_BLOCKED = "AI_ANALYSIS_BLOCKED"
    INVALID = "INVALID"


class SourceAnalysisStatus(StrEnum):
    PLANNED = "PLANNED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AnalysisProfile(StrEnum):
    ECONOMY = "ECONOMY"
    BALANCED = "BALANCED"
    QUALITY = "QUALITY"


class ShortFormConceptStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ShortFormClipRole(StrEnum):
    HOOK = "HOOK"
    CONTEXT = "CONTEXT"
    PROCESS = "PROCESS"
    TRANSFORMATION = "TRANSFORMATION"
    RESULT = "RESULT"


class SourceRightsReview(Contract):
    rights_status: RightsStatus
    license_preset: SourceLicensePreset | None = None
    license_name: NonEmpty | None = None
    license_url: HttpUrl | None = None
    creator: Annotated[str, Field(max_length=500)] | None = None
    commercial_use_allowed: bool | None = None
    derivative_works_allowed: bool | None = None
    attribution_required: bool | None = None
    share_alike_required: bool | None = None
    attribution_text: Annotated[str, Field(max_length=4000)] | None = None
    evidence_reference: Annotated[str, Field(max_length=2000)] | None = None
    reviewer: NonEmpty
    notes: Annotated[str, Field(max_length=4000)] = ""

    @model_validator(mode="after")
    def require_verified_evidence(self) -> "SourceRightsReview":
        if self.rights_status == RightsStatus.VERIFIED:
            if not self.license_name or not self.evidence_reference:
                raise ValueError("Verified source rights require a license and evidence reference")
            if self.commercial_use_allowed is not True or self.derivative_works_allowed is not True:
                raise ValueError("Verified source rights require commercial and derivative use")
            if self.attribution_required is None:
                raise ValueError("Verified source rights require an attribution decision")
            if self.attribution_required and not (self.creator and self.attribution_text):
                raise ValueError("Required creator and attribution text are missing")
        return self


class SourceLicensePresetRead(Contract):
    preset: SourceLicensePreset
    license_name: str | None
    license_url: str | None
    commercial_use_allowed: bool | None
    derivative_works_allowed: bool | None
    attribution_required: bool | None
    share_alike_required: bool | None
    rights_status: RightsStatus = RightsStatus.UNKNOWN


class SourceRightsRead(Contract):
    source_id: int
    rights_status: RightsStatus
    license_preset: SourceLicensePreset | None
    license_name: str | None
    license_url: str | None
    creator: str | None
    commercial_use_allowed: bool | None
    derivative_works_allowed: bool | None
    attribution_required: bool | None
    share_alike_required: bool | None
    attribution_text: str | None
    evidence_reference: str | None
    reviewed_by: str | None
    verified_at: datetime | None
    notes: str

    @computed_field
    @property
    def is_cleared_for_commercial_publication(self) -> bool:
        from app.services.rights.policy import is_cleared_for_commercial_publication

        return is_cleared_for_commercial_publication(self)

    @computed_field
    @property
    def publication_clearance_label(self) -> str:
        from app.services.rights.policy import publication_clearance_label

        return publication_clearance_label(self)


class SourceSceneRead(Contract):
    id: int
    source_id: int
    position: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    representative_frame_path: str | None
    technical_metadata: dict[str, Any]
    heuristic_score: float | None


class TranscriptSegmentInput(Contract):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: Annotated[str, Field(min_length=1, max_length=10000)]
    confidence: Confidence | None = None
    language: Annotated[str, Field(min_length=2, max_length=20)] = "und"

    @model_validator(mode="after")
    def validate_interval(self) -> "TranscriptSegmentInput":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Transcript segment end must be after start")
        return self


class SourceTranscriptionRequest(Contract):
    provider: NonEmpty = "mock"
    model: NonEmpty = "fixture"
    language: Annotated[str, Field(min_length=2, max_length=20)] = "und"
    segments: list[TranscriptSegmentInput] = Field(default_factory=list, max_length=10000)


class TranscriptSegmentRead(TranscriptSegmentInput):
    id: int
    position: int


class SourceTranscriptRead(Contract):
    id: int
    source_id: int
    provider: str
    model: str
    language: str
    status: str
    full_text: str
    created_at: datetime
    segments: list[TranscriptSegmentRead]


class ProcessStageRead(Contract):
    id: int
    source_id: int
    analysis_id: int
    name: str
    start_seconds: float
    end_seconds: float
    description: str
    visual_description: str
    transcript_context: str
    confidence: float
    stage_order: int


class InterestingMomentRead(Contract):
    id: int
    source_id: int
    analysis_id: int
    stage_id: int | None
    start_seconds: float
    end_seconds: float
    proposed_start_seconds: float
    proposed_end_seconds: float
    description: str
    visual_interest_score: float
    educational_value: float
    transformation_score: float
    motion_score: float
    machinery_score: float
    hook_score: float
    loop_potential: float
    confidence: float
    evidence: list[dict[str, Any]]


class SourceAnalysisRequest(Contract):
    profile: AnalysisProfile = AnalysisProfile.BALANCED
    use_external_ai: bool = False


class SourceAnalysisRead(Contract):
    id: int
    source_id: int
    status: SourceAnalysisStatus
    profile: AnalysisProfile
    provider: str
    model: str
    version: str
    detected_process: str | None
    summary: str
    confidence: float | None
    warnings: list[str]
    budget: dict[str, Any]
    created_at: datetime
    stages: list[ProcessStageRead]
    moments: list[InterestingMomentRead]


class ShortFormClipInput(Contract):
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    output_order: int = Field(ge=0)
    target_output_duration: float = Field(gt=0, le=60)
    speed_recommendation: float = Field(1.0, gt=0, le=2)
    role: ShortFormClipRole = ShortFormClipRole.PROCESS
    moment_id: int | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> "ShortFormClipInput":
        if self.source_end <= self.source_start:
            raise ValueError("Clip end must be after start")
        return self


class ShortFormClipRead(ShortFormClipInput):
    id: int
    concept_id: int


class ConceptGenerationRequest(Contract):
    count: int = Field(3, ge=1, le=5)
    target_duration: float = Field(45, ge=15, le=60)


class ConceptClipPatch(Contract):
    clips: list[ShortFormClipInput] = Field(min_length=1, max_length=20)


class ShortFormConceptRead(Contract):
    id: int
    source_id: int
    analysis_id: int
    candidate_id: int | None
    title: str
    angle: str
    target_duration: float
    hook_candidate: str
    stage_refs: list[int]
    moment_refs: list[int]
    coverage: dict[str, Any]
    status: ShortFormConceptStatus
    generation_version: str
    overlap_warning: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_notes: str
    created_at: datetime
    clips: list[ShortFormClipRead]


class LongFormSourceRead(Contract):
    id: int
    title: str
    source_type: LongFormSourceType
    original_filename: str
    relative_path: str
    checksum_sha256: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None
    has_audio: bool
    container: str
    rotation: int
    status: LongFormSourceStatus
    analysis_status: SourceAnalysisStatus | None
    created_at: datetime
    rights: SourceRightsRead
    scenes: list[SourceSceneRead] = Field(default_factory=list)
    transcripts: list[SourceTranscriptRead] = Field(default_factory=list)
    analyses: list[SourceAnalysisRead] = Field(default_factory=list)
    concepts: list[ShortFormConceptRead] = Field(default_factory=list)


class CaptionItemRead(Contract):
    id: int
    position: int
    start_seconds: float
    end_seconds: float
    display_text: str
    spoken_text: str
    sentence_id: int
    beat_id: int
    timing_method: CaptionTimingMethod
    position_name: CaptionPosition
    style: dict[str, Any]
    emphasis_spans: list[dict[str, Any]]
    characters_per_second: float
    words_per_minute: float
    warnings: list[str]


class GraphicOverlayRead(Contract):
    id: int
    overlay_type: GraphicOverlayType
    text: str
    start_seconds: float
    end_seconds: float
    position_name: CaptionPosition
    style_profile: CaptionStyleProfile
    claim_ids: list[int]
    beat_ids: list[int]
    z_index: int


class CaptionPlanRead(Contract):
    id: int
    render_asset_id: int
    script_id: int
    narration_id: int
    language: str
    target_platform: TargetPlatform
    style_profile: CaptionStyleProfile
    segmentation_strategy: str
    timing_method: CaptionTimingMethod
    emphasis_mode: EmphasisMode
    safe_area: dict[str, float]
    planner_version: str
    warnings: list[str]
    cache_key: str
    created_at: datetime
    items: list[CaptionItemRead]
    overlays: list[GraphicOverlayRead]


class FinalRenderReviewRead(Contract):
    id: int
    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str
    notes: str
    created_at: datetime


class FinalRenderAssetRead(Contract):
    id: int
    caption_plan_id: int
    raw_render_id: int
    output_path: str | None
    ass_path: str | None
    srt_path: str | None
    preview_path: str | None
    width: int | None
    height: int | None
    fps: float | None
    duration: float | None
    video_codec: str | None
    audio_codec: str | None
    file_size_bytes: int | None
    checksum_sha256: str | None
    render_version: str
    subtitle_renderer_version: str
    ffmpeg_version: str | None
    status: FinalRenderStatus
    warnings: list[str]
    cache_key: str
    failure_reason: str
    process_exit_code: int | None
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str
    reviews: list[FinalRenderReviewRead]


class FinalRenderResponse(Contract):
    render: FinalRenderAssetRead
    reused: bool
