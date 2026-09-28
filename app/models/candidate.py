from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime, utcnow
from app.schemas.domain import (
    AlignmentMethod,
    BeatKind,
    CandidateStatus,
    CaptionPosition,
    CaptionStyleProfile,
    CaptionTimingMethod,
    ClaimStatus,
    CompositionStrategy,
    DurationStatus,
    EmphasisMode,
    EvidenceRelation,
    FinalRenderStatus,
    GraphicOverlayType,
    KnowledgeType,
    NarrationStatus,
    Orientation,
    RenderStatus,
    ResearchStatus,
    RightsStatus,
    ScriptFactualityType,
    ScriptStatus,
    ScriptStyle,
    SourceAudioPolicy,
    SourceTier,
    SourceType,
    TargetPlatform,
    TransitionType,
)


def enum_column(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda values: [item.value for item in values],
    )


class VideoCandidate(Base):
    __tablename__ = "video_candidates"
    __table_args__ = (
        UniqueConstraint("provider", "provider_video_id", name="uq_candidate_provider_video"),
        Index("ix_candidate_status_score", "status", "total_score"),
        CheckConstraint(
            "total_score IS NULL OR (total_score >= 0 AND total_score <= 100)",
            name="ck_candidate_total_score",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_video_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float | None]
    width: Mapped[int | None]
    height: Mapped[int | None]
    orientation: Mapped[Orientation] = mapped_column(enum_column(Orientation, "orientation"))
    author: Mapped[str | None] = mapped_column(Text)
    author_url: Mapped[str | None] = mapped_column(Text)
    search_query: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200), index=True)
    industrial_process: Mapped[str | None] = mapped_column(String(200), index=True)
    object_being_manufactured: Mapped[str] = mapped_column(String(200))
    visual_score: Mapped[float | None]
    transformation_score: Mapped[float | None]
    machine_score: Mapped[float | None]
    hook_score: Mapped[float | None]
    loop_score: Mapped[float | None]
    educational_score: Mapped[float | None]
    total_score: Mapped[float | None]
    score_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[CandidateStatus] = mapped_column(
        enum_column(CandidateStatus, "candidate_status"), default=CandidateStatus.DISCOVERED
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}

    rights: Mapped["RightsRecord"] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="joined", uselist=False
    )
    events: Mapped[list["CandidateEvent"]] = relationship(cascade="all, delete-orphan")
    evaluations: Mapped[list["ScoreEvaluation"]] = relationship(cascade="all, delete-orphan")
    research_dossiers: Mapped[list["ResearchDossier"]] = relationship(cascade="all, delete-orphan")
    script_drafts: Mapped[list["ScriptDraft"]] = relationship(cascade="all, delete-orphan")
    edit_plans: Mapped[list["VideoEditPlan"]] = relationship(cascade="all, delete-orphan")
    render_assets: Mapped[list["RenderAsset"]] = relationship(cascade="all, delete-orphan")

    @property
    def license_name(self) -> str | None:
        return self.rights.license_name

    @property
    def license_url(self) -> str | None:
        return self.rights.license_url

    @property
    def commercial_use_allowed(self) -> bool | None:
        return self.rights.commercial_use_allowed

    @property
    def attribution_required(self) -> bool | None:
        return self.rights.attribution_required

    @property
    def rights_status(self) -> RightsStatus:
        return self.rights.rights_status


class RightsRecord(Base):
    __tablename__ = "rights_records"
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text)
    creator: Mapped[str | None] = mapped_column(Text)
    license_name: Mapped[str | None] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(Text)
    commercial_use_allowed: Mapped[bool | None]
    modification_allowed: Mapped[bool | None]
    attribution_required: Mapped[bool | None]
    attribution_text: Mapped[str | None] = mapped_column(Text)
    rights_status: Mapped[RightsStatus] = mapped_column(
        enum_column(RightsStatus, "rights_status"), default=RightsStatus.UNKNOWN
    )
    verification_date: Mapped[datetime | None] = mapped_column(UTCDateTime())
    verified_by: Mapped[str | None] = mapped_column(String(200))
    evidence_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    candidate: Mapped[VideoCandidate] = relationship(back_populates="rights")


class CandidateEvent(Base):
    __tablename__ = "candidate_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(60))
    reviewer: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SearchCache(Base):
    __tablename__ = "search_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class ScoreEvaluation(Base):
    __tablename__ = "score_evaluations"
    __table_args__ = (
        Index("ix_score_evaluation_candidate_created", "candidate_id", "created_at"),
        Index(
            "ix_score_evaluation_reuse",
            "candidate_id",
            "method",
            "ai_provider",
            "ai_model",
            "prompt_version",
            "scorer_version",
        ),
        CheckConstraint(
            "total_score >= 0 AND total_score <= 100", name="ck_evaluation_total_score"
        ),
        CheckConstraint(
            "overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)",
            name="ck_evaluation_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(40), index=True)
    scorer_version: Mapped[str] = mapped_column(String(40))
    ai_provider: Mapped[str | None] = mapped_column(String(60))
    ai_model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    weights: Mapped[dict[str, float]] = mapped_column(JSON)
    ratings: Mapped[dict[str, float]] = mapped_column(JSON)
    total_score: Mapped[float]
    overall_confidence: Mapped[float | None]
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ResearchDossier(Base):
    __tablename__ = "research_dossiers"
    __table_args__ = (
        Index("ix_research_dossier_candidate_created", "candidate_id", "created_at"),
        Index(
            "ix_research_dossier_reuse",
            "candidate_id",
            "search_provider",
            "research_provider",
            "model",
            "prompt_version",
            "research_version",
        ),
        CheckConstraint(
            "process_confidence >= 0 AND process_confidence <= 1",
            name="ck_research_process_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ResearchStatus] = mapped_column(
        enum_column(ResearchStatus, "research_status"), index=True
    )
    detected_object: Mapped[str | None] = mapped_column(String(300))
    detected_process: Mapped[str | None] = mapped_column(String(300))
    visual_observations: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    alternate_process_hypotheses: Mapped[list[str]] = mapped_column(JSON)
    research_questions: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    search_queries: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    research_summary: Mapped[str] = mapped_column(Text)
    verified_process_name: Mapped[str | None] = mapped_column(String(300))
    process_confidence: Mapped[float]
    material: Mapped[str | None] = mapped_column(String(300))
    machine_types: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    process_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    technical_explanations: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    interesting_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    safety_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    unknowns: Mapped[list[str]] = mapped_column(JSON)
    search_provider: Mapped[str] = mapped_column(String(60))
    research_provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(40))
    research_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_notes: Mapped[str] = mapped_column(Text, default="")

    sources: Mapped[list["ResearchSource"]] = relationship(
        cascade="all, delete-orphan", order_by="ResearchSource.id"
    )
    claims: Mapped[list["ResearchClaim"]] = relationship(
        cascade="all, delete-orphan", order_by="ResearchClaim.id"
    )
    contradictions: Mapped[list["ResearchContradiction"]] = relationship(
        cascade="all, delete-orphan", order_by="ResearchContradiction.id"
    )


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("dossier_id", "url", name="uq_research_source_dossier_url"),
        CheckConstraint("relevance >= 0 AND relevance <= 1", name="ck_source_relevance"),
        CheckConstraint("credibility >= 0 AND credibility <= 1", name="ck_source_credibility"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("research_dossiers.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500))
    publisher: Mapped[str] = mapped_column(String(300))
    author: Mapped[str | None] = mapped_column(String(300))
    publication_date: Mapped[str | None] = mapped_column(String(80))
    accessed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    source_type: Mapped[SourceType] = mapped_column(enum_column(SourceType, "source_type"))
    tier: Mapped[SourceTier] = mapped_column(enum_column(SourceTier, "source_tier"))
    relevance: Mapped[float]
    credibility: Mapped[float]
    search_snippet: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class ResearchClaim(Base):
    __tablename__ = "research_claims"
    __table_args__ = (
        CheckConstraint(
            "source_support_confidence >= 0 AND source_support_confidence <= 1",
            name="ck_claim_source_confidence",
        ),
        CheckConstraint(
            "video_applicability_confidence >= 0 AND video_applicability_confidence <= 1",
            name="ck_claim_video_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("research_dossiers.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[ClaimStatus] = mapped_column(enum_column(ClaimStatus, "claim_status"))
    knowledge_type: Mapped[KnowledgeType] = mapped_column(
        enum_column(KnowledgeType, "knowledge_type")
    )
    source_support_confidence: Mapped[float]
    video_applicability_confidence: Mapped[float]
    quantitative: Mapped[bool]
    notes: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list["ResearchClaimEvidence"]] = relationship(
        cascade="all, delete-orphan", order_by="ResearchClaimEvidence.id"
    )


class ResearchClaimEvidence(Base):
    __tablename__ = "research_claim_evidence"
    __table_args__ = (
        CheckConstraint(
            "source_says_confidence >= 0 AND source_says_confidence <= 1",
            name="ck_claim_evidence_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[EvidenceRelation] = mapped_column(
        enum_column(EvidenceRelation, "evidence_relation")
    )
    excerpt: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(500))
    source_says_confidence: Mapped[float]
    source: Mapped[ResearchSource] = relationship()


class ResearchContradiction(Base):
    __tablename__ = "research_contradictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("research_dossiers.id", ondelete="CASCADE"), index=True
    )
    claim_ids: Mapped[list[int]] = mapped_column(JSON)
    source_ids: Mapped[list[int]] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text)
    possible_explanations: Mapped[list[str]] = mapped_column(JSON)
    resolved: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class ScriptDraft(Base):
    __tablename__ = "script_drafts"
    __table_args__ = (
        Index("ix_script_candidate_created", "candidate_id", "created_at"),
        Index("ix_script_reuse", "cache_key", "batch_key"),
        CheckConstraint("word_count >= 1", name="ck_script_word_count"),
        CheckConstraint("estimated_duration_seconds > 0", name="ck_script_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), index=True
    )
    research_dossier_id: Mapped[int] = mapped_column(
        ForeignKey("research_dossiers.id", ondelete="RESTRICT"), index=True
    )
    visual_evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("score_evaluations.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[ScriptStatus] = mapped_column(
        enum_column(ScriptStatus, "script_status"), index=True
    )
    style: Mapped[ScriptStyle] = mapped_column(enum_column(ScriptStyle, "script_style"))
    target_platform: Mapped[TargetPlatform] = mapped_column(
        enum_column(TargetPlatform, "target_platform")
    )
    language: Mapped[str] = mapped_column(String(10))
    target_duration_seconds: Mapped[int]
    estimated_duration_seconds: Mapped[float]
    word_count: Mapped[int]
    speaking_rate: Mapped[float]
    title: Mapped[str] = mapped_column(String(200))
    hook: Mapped[str] = mapped_column(Text)
    closing: Mapped[str] = mapped_column(Text)
    full_narration: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(40))
    script_version: Mapped[str] = mapped_column(String(40))
    variant_index: Mapped[int]
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    batch_key: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_notes: Mapped[str] = mapped_column(Text, default="")

    beats: Mapped[list["ScriptBeat"]] = relationship(
        cascade="all, delete-orphan", order_by="ScriptBeat.position"
    )
    reviews: Mapped[list["ScriptReview"]] = relationship(
        cascade="all, delete-orphan", order_by="ScriptReview.created_at"
    )
    narrations: Mapped[list["NarrationAsset"]] = relationship(
        cascade="all, delete-orphan", order_by="NarrationAsset.created_at"
    )
    edit_plans: Mapped[list["VideoEditPlan"]] = relationship(cascade="all, delete-orphan")


class ScriptBeat(Base):
    __tablename__ = "script_beats"
    __table_args__ = (
        UniqueConstraint("script_id", "position", name="uq_script_beat_position"),
        CheckConstraint("recommended_end > recommended_start", name="ck_beat_interval"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("script_drafts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    kind: Mapped[BeatKind] = mapped_column(enum_column(BeatKind, "script_beat_kind"))
    recommended_start: Mapped[float]
    recommended_end: Mapped[float]
    visual_direction: Mapped[str] = mapped_column(Text)
    sentences: Mapped[list["ScriptSentence"]] = relationship(
        cascade="all, delete-orphan", order_by="ScriptSentence.position"
    )


class ScriptSentence(Base):
    __tablename__ = "script_sentences"
    __table_args__ = (UniqueConstraint("beat_id", "position", name="uq_sentence_position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    beat_id: Mapped[int] = mapped_column(
        ForeignKey("script_beats.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    display_text: Mapped[str] = mapped_column(Text)
    spoken_text: Mapped[str] = mapped_column(Text)
    factuality_type: Mapped[ScriptFactualityType] = mapped_column(
        enum_column(ScriptFactualityType, "script_factuality_type")
    )
    claim_refs: Mapped[list["ScriptSentenceClaim"]] = relationship(
        cascade="all, delete-orphan", order_by="ScriptSentenceClaim.claim_id"
    )
    visual_refs: Mapped[list["ScriptSentenceVisualRef"]] = relationship(
        cascade="all, delete-orphan", order_by="ScriptSentenceVisualRef.visual_ref"
    )


class ScriptSentenceClaim(Base):
    __tablename__ = "script_sentence_claims"
    sentence_id: Mapped[int] = mapped_column(
        ForeignKey("script_sentences.id", ondelete="CASCADE"), primary_key=True
    )
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("research_claims.id", ondelete="RESTRICT"), primary_key=True
    )


class ScriptSentenceVisualRef(Base):
    __tablename__ = "script_sentence_visual_refs"
    sentence_id: Mapped[int] = mapped_column(
        ForeignKey("script_sentences.id", ondelete="CASCADE"), primary_key=True
    )
    visual_ref: Mapped[str] = mapped_column(String(80), primary_key=True)
    start: Mapped[float]
    end: Mapped[float]


class ScriptReview(Base):
    __tablename__ = "script_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("script_drafts.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (
        UniqueConstraint("provider", "provider_voice_id", name="uq_voice_provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(60), index=True)
    provider_voice_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class NarrationAsset(Base):
    __tablename__ = "narration_assets"
    __table_args__ = (
        Index("ix_narration_script_created", "script_id", "created_at"),
        Index("ix_narration_reuse", "cache_key"),
        CheckConstraint("duration_seconds > 0", name="ck_narration_duration"),
        CheckConstraint("file_size_bytes > 0", name="ck_narration_file_size"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("script_drafts.id", ondelete="CASCADE"), index=True
    )
    voice_profile_id: Mapped[int] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(200))
    voice_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_text_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_character_count: Mapped[int]
    audio_format: Mapped[str] = mapped_column(String(30))
    codec: Mapped[str | None] = mapped_column(String(40))
    sample_rate: Mapped[int | None]
    channels: Mapped[int | None]
    duration_seconds: Mapped[float]
    estimated_duration_seconds: Mapped[float]
    duration_difference_seconds: Mapped[float]
    duration_difference_percent: Mapped[float]
    duration_status: Mapped[DurationStatus] = mapped_column(
        enum_column(DurationStatus, "narration_duration_status")
    )
    file_size_bytes: Mapped[int]
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(500), unique=True)
    generation_version: Mapped[str] = mapped_column(String(40))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[NarrationStatus] = mapped_column(
        enum_column(NarrationStatus, "narration_status"), index=True
    )
    alignment_method: Mapped[AlignmentMethod] = mapped_column(
        enum_column(AlignmentMethod, "narration_alignment_method")
    )
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_notes: Mapped[str] = mapped_column(Text, default="")

    voice_profile: Mapped[VoiceProfile] = relationship()
    alignments: Mapped[list["NarrationAlignment"]] = relationship(
        cascade="all, delete-orphan", order_by="NarrationAlignment.start_seconds"
    )
    reviews: Mapped[list["NarrationReview"]] = relationship(
        cascade="all, delete-orphan", order_by="NarrationReview.created_at"
    )


class NarrationAlignment(Base):
    __tablename__ = "narration_alignments"
    __table_args__ = (
        UniqueConstraint("narration_id", "sentence_id", name="uq_alignment_sentence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    narration_id: Mapped[int] = mapped_column(
        ForeignKey("narration_assets.id", ondelete="CASCADE"), index=True
    )
    sentence_id: Mapped[int] = mapped_column(
        ForeignKey("script_sentences.id", ondelete="RESTRICT"), index=True
    )
    beat_id: Mapped[int] = mapped_column(
        ForeignKey("script_beats.id", ondelete="RESTRICT"), index=True
    )
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    method: Mapped[AlignmentMethod] = mapped_column(
        enum_column(AlignmentMethod, "narration_alignment_method")
    )
    confidence: Mapped[float | None]
    word_timings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=list)


class NarrationReview(Base):
    __tablename__ = "narration_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    narration_id: Mapped[int] = mapped_column(
        ForeignKey("narration_assets.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class VideoEditPlan(Base):
    __tablename__ = "video_edit_plans"
    __table_args__ = (
        Index("ix_edit_plan_candidate_created", "candidate_id", "created_at"),
        Index("ix_edit_plan_reuse", "cache_key"),
        CheckConstraint("target_duration > 0", name="ck_edit_plan_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), index=True
    )
    script_id: Mapped[int] = mapped_column(
        ForeignKey("script_drafts.id", ondelete="RESTRICT"), index=True
    )
    narration_id: Mapped[int] = mapped_column(
        ForeignKey("narration_assets.id", ondelete="RESTRICT"), index=True
    )
    target_platform: Mapped[TargetPlatform] = mapped_column(
        enum_column(TargetPlatform, "edit_plan_target_platform")
    )
    aspect_ratio: Mapped[str] = mapped_column(String(20))
    width: Mapped[int]
    height: Mapped[int]
    fps: Mapped[float]
    target_duration: Mapped[float]
    actual_narration_duration: Mapped[float]
    composition: Mapped[CompositionStrategy] = mapped_column(
        enum_column(CompositionStrategy, "composition_strategy")
    )
    source_audio_policy: Mapped[SourceAudioPolicy] = mapped_column(
        enum_column(SourceAudioPolicy, "source_audio_policy")
    )
    audio_configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    render_configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    planner_version: Mapped[str] = mapped_column(String(40))
    warnings: Mapped[list[str]] = mapped_column(JSON)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    segments: Mapped[list["EditSegment"]] = relationship(
        cascade="all, delete-orphan", order_by="EditSegment.position"
    )
    renders: Mapped[list["RenderAsset"]] = relationship(cascade="all, delete-orphan")


class EditSegment(Base):
    __tablename__ = "edit_segments"
    __table_args__ = (
        UniqueConstraint("edit_plan_id", "position", name="uq_edit_segment_position"),
        CheckConstraint("source_end > source_start", name="ck_edit_segment_source_interval"),
        CheckConstraint("output_end > output_start", name="ck_edit_segment_output_interval"),
        CheckConstraint("playback_speed > 0", name="ck_edit_segment_speed"),
        CheckConstraint("loop_count >= 1", name="ck_edit_segment_loop_count"),
        CheckConstraint("hold_seconds >= 0", name="ck_edit_segment_hold"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    edit_plan_id: Mapped[int] = mapped_column(
        ForeignKey("video_edit_plans.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    source_start: Mapped[float]
    source_end: Mapped[float]
    output_start: Mapped[float]
    output_end: Mapped[float]
    playback_speed: Mapped[float]
    composition: Mapped[CompositionStrategy] = mapped_column(
        enum_column(CompositionStrategy, "edit_segment_composition_strategy")
    )
    beat_id: Mapped[int] = mapped_column(
        ForeignKey("script_beats.id", ondelete="RESTRICT"), index=True
    )
    sentence_ids: Mapped[list[int]] = mapped_column(JSON)
    visual_refs: Mapped[list[str]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    transition: Mapped[TransitionType] = mapped_column(
        enum_column(TransitionType, "edit_transition_type")
    )
    loop_count: Mapped[int]
    hold_seconds: Mapped[float]


class RenderAsset(Base):
    __tablename__ = "render_assets"
    __table_args__ = (
        Index("ix_render_candidate_created", "candidate_id", "created_at"),
        Index("ix_render_reuse", "cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="CASCADE"), index=True
    )
    script_id: Mapped[int] = mapped_column(
        ForeignKey("script_drafts.id", ondelete="RESTRICT"), index=True
    )
    narration_id: Mapped[int] = mapped_column(
        ForeignKey("narration_assets.id", ondelete="RESTRICT"), index=True
    )
    edit_plan_id: Mapped[int] = mapped_column(
        ForeignKey("video_edit_plans.id", ondelete="CASCADE"), index=True
    )
    output_path: Mapped[str | None] = mapped_column(String(500), unique=True)
    width: Mapped[int | None]
    height: Mapped[int | None]
    fps: Mapped[float | None]
    duration: Mapped[float | None]
    planned_duration: Mapped[float]
    duration_difference: Mapped[float | None]
    video_codec: Mapped[str | None] = mapped_column(String(80))
    audio_codec: Mapped[str | None] = mapped_column(String(80))
    file_size_bytes: Mapped[int | None]
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    render_version: Mapped[str] = mapped_column(String(40))
    ffmpeg_version: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[RenderStatus] = mapped_column(
        enum_column(RenderStatus, "render_status"), index=True
    )
    warnings: Mapped[list[str]] = mapped_column(JSON)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    process_exit_code: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_notes: Mapped[str] = mapped_column(Text, default="")

    reviews: Mapped[list["RenderReview"]] = relationship(
        cascade="all, delete-orphan", order_by="RenderReview.created_at"
    )


class RenderReview(Base):
    __tablename__ = "render_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    render_id: Mapped[int] = mapped_column(
        ForeignKey("render_assets.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CaptionPlan(Base):
    __tablename__ = "caption_plans"
    __table_args__ = (
        Index("ix_caption_plan_render_created", "render_asset_id", "created_at"),
        Index("ix_caption_plan_reuse", "cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    render_asset_id: Mapped[int] = mapped_column(
        ForeignKey("render_assets.id", ondelete="CASCADE"), index=True
    )
    script_id: Mapped[int] = mapped_column(
        ForeignKey("script_drafts.id", ondelete="RESTRICT"), index=True
    )
    narration_id: Mapped[int] = mapped_column(
        ForeignKey("narration_assets.id", ondelete="RESTRICT"), index=True
    )
    language: Mapped[str] = mapped_column(String(10))
    target_platform: Mapped[TargetPlatform] = mapped_column(
        enum_column(TargetPlatform, "caption_target_platform")
    )
    style_profile: Mapped[CaptionStyleProfile] = mapped_column(
        enum_column(CaptionStyleProfile, "caption_style_profile")
    )
    segmentation_strategy: Mapped[str] = mapped_column(String(40))
    timing_method: Mapped[CaptionTimingMethod] = mapped_column(
        enum_column(CaptionTimingMethod, "caption_timing_method")
    )
    emphasis_mode: Mapped[EmphasisMode] = mapped_column(
        enum_column(EmphasisMode, "caption_emphasis_mode")
    )
    safe_area: Mapped[dict[str, float]] = mapped_column(JSON)
    planner_version: Mapped[str] = mapped_column(String(40))
    warnings: Mapped[list[str]] = mapped_column(JSON)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    items: Mapped[list["CaptionItem"]] = relationship(
        cascade="all, delete-orphan", order_by="CaptionItem.position"
    )
    overlays: Mapped[list["GraphicOverlay"]] = relationship(
        cascade="all, delete-orphan", order_by="GraphicOverlay.z_index"
    )
    final_renders: Mapped[list["FinalRenderAsset"]] = relationship(cascade="all, delete-orphan")


class CaptionItem(Base):
    __tablename__ = "caption_items"
    __table_args__ = (
        UniqueConstraint("caption_plan_id", "position", name="uq_caption_item_position"),
        CheckConstraint("end_seconds > start_seconds", name="ck_caption_item_interval"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    caption_plan_id: Mapped[int] = mapped_column(
        ForeignKey("caption_plans.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    display_text: Mapped[str] = mapped_column(Text)
    spoken_text: Mapped[str] = mapped_column(Text)
    sentence_id: Mapped[int] = mapped_column(
        ForeignKey("script_sentences.id", ondelete="RESTRICT"), index=True
    )
    beat_id: Mapped[int] = mapped_column(
        ForeignKey("script_beats.id", ondelete="RESTRICT"), index=True
    )
    timing_method: Mapped[CaptionTimingMethod] = mapped_column(
        enum_column(CaptionTimingMethod, "caption_item_timing_method")
    )
    position_name: Mapped[CaptionPosition] = mapped_column(
        enum_column(CaptionPosition, "caption_position")
    )
    style: Mapped[dict[str, Any]] = mapped_column(JSON)
    emphasis_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    characters_per_second: Mapped[float]
    words_per_minute: Mapped[float]
    warnings: Mapped[list[str]] = mapped_column(JSON)


class GraphicOverlay(Base):
    __tablename__ = "graphic_overlays"
    __table_args__ = (
        CheckConstraint("end_seconds > start_seconds", name="ck_graphic_overlay_interval"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    caption_plan_id: Mapped[int] = mapped_column(
        ForeignKey("caption_plans.id", ondelete="CASCADE"), index=True
    )
    overlay_type: Mapped[GraphicOverlayType] = mapped_column(
        enum_column(GraphicOverlayType, "graphic_overlay_type")
    )
    text: Mapped[str] = mapped_column(Text)
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    position_name: Mapped[CaptionPosition] = mapped_column(
        enum_column(CaptionPosition, "graphic_overlay_position")
    )
    style_profile: Mapped[CaptionStyleProfile] = mapped_column(
        enum_column(CaptionStyleProfile, "graphic_overlay_style")
    )
    claim_ids: Mapped[list[int]] = mapped_column(JSON)
    beat_ids: Mapped[list[int]] = mapped_column(JSON)
    z_index: Mapped[int]


class FinalRenderAsset(Base):
    __tablename__ = "final_render_assets"
    __table_args__ = (
        Index("ix_final_render_plan_created", "caption_plan_id", "created_at"),
        Index("ix_final_render_reuse", "cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    caption_plan_id: Mapped[int] = mapped_column(
        ForeignKey("caption_plans.id", ondelete="CASCADE"), index=True
    )
    raw_render_id: Mapped[int] = mapped_column(
        ForeignKey("render_assets.id", ondelete="RESTRICT"), index=True
    )
    output_path: Mapped[str | None] = mapped_column(String(500), unique=True)
    ass_path: Mapped[str | None] = mapped_column(String(500), unique=True)
    srt_path: Mapped[str | None] = mapped_column(String(500), unique=True)
    preview_path: Mapped[str | None] = mapped_column(String(500), unique=True)
    width: Mapped[int | None]
    height: Mapped[int | None]
    fps: Mapped[float | None]
    duration: Mapped[float | None]
    video_codec: Mapped[str | None] = mapped_column(String(80))
    audio_codec: Mapped[str | None] = mapped_column(String(80))
    file_size_bytes: Mapped[int | None]
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    render_version: Mapped[str] = mapped_column(String(40))
    subtitle_renderer_version: Mapped[str] = mapped_column(String(40))
    ffmpeg_version: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[FinalRenderStatus] = mapped_column(
        enum_column(FinalRenderStatus, "final_render_status"), index=True
    )
    warnings: Mapped[list[str]] = mapped_column(JSON)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    process_exit_code: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_notes: Mapped[str] = mapped_column(Text, default="")

    reviews: Mapped[list["FinalRenderReview"]] = relationship(
        cascade="all, delete-orphan", order_by="FinalRenderReview.created_at"
    )


class FinalRenderReview(Base):
    __tablename__ = "final_render_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    final_render_id: Mapped[int] = mapped_column(
        ForeignKey("final_render_assets.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
