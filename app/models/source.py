from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime, utcnow
from app.models.candidate import enum_column
from app.schemas.domain import (
    AnalysisProfile,
    LongFormSourceStatus,
    LongFormSourceType,
    RightsStatus,
    ShortFormClipRole,
    ShortFormConceptStatus,
    SourceAnalysisStatus,
    SourceLicensePreset,
)


class LongFormSource(Base):
    __tablename__ = "long_form_sources"
    __table_args__ = (
        Index("ix_long_form_source_status_created", "status", "created_at"),
        CheckConstraint("duration_seconds > 0", name="ck_long_source_duration"),
        CheckConstraint("size_bytes > 0", name="ck_long_source_size"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[LongFormSourceType] = mapped_column(
        enum_column(LongFormSourceType, "long_form_source_type")
    )
    original_filename: Mapped[str] = mapped_column(String(500))
    relative_path: Mapped[str] = mapped_column(String(500), unique=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int]
    duration_seconds: Mapped[float]
    width: Mapped[int]
    height: Mapped[int]
    fps: Mapped[float]
    video_codec: Mapped[str] = mapped_column(String(80))
    audio_codec: Mapped[str | None] = mapped_column(String(80))
    has_audio: Mapped[bool]
    container: Mapped[str] = mapped_column(String(200))
    rotation: Mapped[int] = mapped_column(default=0)
    status: Mapped[LongFormSourceStatus] = mapped_column(
        enum_column(LongFormSourceStatus, "long_form_source_status"),
        default=LongFormSourceStatus.INGESTED,
    )
    analysis_status: Mapped[SourceAnalysisStatus | None] = mapped_column(
        enum_column(SourceAnalysisStatus, "source_analysis_status"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    rights: Mapped["SourceRights"] = relationship(
        back_populates="source", cascade="all, delete-orphan", lazy="joined", uselist=False
    )
    scenes: Mapped[list["SourceScene"]] = relationship(
        cascade="all, delete-orphan", order_by="SourceScene.position"
    )
    transcripts: Mapped[list["SourceTranscript"]] = relationship(
        cascade="all, delete-orphan", order_by="SourceTranscript.created_at"
    )
    analyses: Mapped[list["SourceAnalysis"]] = relationship(
        cascade="all, delete-orphan", order_by="SourceAnalysis.created_at"
    )
    concepts: Mapped[list["ShortFormConcept"]] = relationship(
        cascade="all, delete-orphan", order_by="ShortFormConcept.created_at"
    )


class SourceRights(Base):
    __tablename__ = "source_rights"
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), primary_key=True
    )
    rights_status: Mapped[RightsStatus] = mapped_column(
        enum_column(RightsStatus, "source_rights_status"), default=RightsStatus.UNKNOWN
    )
    license_preset: Mapped[SourceLicensePreset | None] = mapped_column(
        enum_column(SourceLicensePreset, "source_license_preset"), nullable=True
    )
    license_name: Mapped[str | None] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(Text)
    creator: Mapped[str | None] = mapped_column(String(500))
    commercial_use_allowed: Mapped[bool | None]
    derivative_works_allowed: Mapped[bool | None]
    attribution_required: Mapped[bool | None]
    share_alike_required: Mapped[bool | None]
    attribution_text: Mapped[str | None] = mapped_column(Text)
    evidence_reference: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[LongFormSource] = relationship(back_populates="rights")

    @property
    def is_cleared_for_commercial_publication(self) -> bool:
        from app.services.rights.policy import is_cleared_for_commercial_publication

        return is_cleared_for_commercial_publication(self)

    @property
    def publication_clearance_label(self) -> str:
        from app.services.rights.policy import publication_clearance_label

        return publication_clearance_label(self)


class SourceScene(Base):
    __tablename__ = "source_scenes"
    __table_args__ = (
        CheckConstraint("start_seconds >= 0 AND end_seconds > start_seconds", name="ck_scene_time"),
        Index("ix_source_scene_source_position", "source_id", "position", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    duration_seconds: Mapped[float]
    representative_frame_path: Mapped[str | None] = mapped_column(String(500))
    technical_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    heuristic_score: Mapped[float | None]


class SourceTranscript(Base):
    __tablename__ = "source_transcripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40))
    full_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    segments: Mapped[list["SourceTranscriptSegment"]] = relationship(
        cascade="all, delete-orphan", order_by="SourceTranscriptSegment.position"
    )


class SourceTranscriptSegment(Base):
    __tablename__ = "source_transcript_segments"
    __table_args__ = (
        CheckConstraint(
            "start_seconds >= 0 AND end_seconds > start_seconds", name="ck_transcript_time"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(
        ForeignKey("source_transcripts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int]
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None]
    language: Mapped[str] = mapped_column(String(20))


class SourceAnalysis(Base):
    __tablename__ = "source_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[SourceAnalysisStatus] = mapped_column(
        enum_column(SourceAnalysisStatus, "source_analysis_run_status")
    )
    profile: Mapped[AnalysisProfile] = mapped_column(
        enum_column(AnalysisProfile, "source_analysis_profile")
    )
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(40))
    detected_process: Mapped[str | None] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None]
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    stages: Mapped[list["ProcessStage"]] = relationship(
        cascade="all, delete-orphan", order_by="ProcessStage.stage_order"
    )
    moments: Mapped[list["InterestingMoment"]] = relationship(
        cascade="all, delete-orphan", order_by="InterestingMoment.start_seconds"
    )


class ProcessStage(Base):
    __tablename__ = "process_stages"
    __table_args__ = (
        CheckConstraint("start_seconds >= 0 AND end_seconds > start_seconds", name="ck_stage_time"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("source_analyses.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    description: Mapped[str] = mapped_column(Text)
    visual_description: Mapped[str] = mapped_column(Text)
    transcript_context: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float]
    stage_order: Mapped[int]


class InterestingMoment(Base):
    __tablename__ = "interesting_moments"
    __table_args__ = (
        CheckConstraint(
            "start_seconds >= 0 AND end_seconds > start_seconds", name="ck_moment_time"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("source_analyses.id", ondelete="CASCADE"), index=True
    )
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("process_stages.id", ondelete="SET NULL"), index=True
    )
    start_seconds: Mapped[float]
    end_seconds: Mapped[float]
    proposed_start_seconds: Mapped[float]
    proposed_end_seconds: Mapped[float]
    description: Mapped[str] = mapped_column(Text)
    visual_interest_score: Mapped[float]
    educational_value: Mapped[float]
    transformation_score: Mapped[float]
    motion_score: Mapped[float]
    machinery_score: Mapped[float]
    hook_score: Mapped[float]
    loop_potential: Mapped[float]
    confidence: Mapped[float]
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class ShortFormConcept(Base):
    __tablename__ = "short_form_concepts"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("long_form_sources.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("source_analyses.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_candidates.id", ondelete="SET NULL"), unique=True
    )
    title: Mapped[str] = mapped_column(String(200))
    angle: Mapped[str] = mapped_column(Text)
    target_duration: Mapped[float]
    hook_candidate: Mapped[str] = mapped_column(Text)
    stage_refs: Mapped[list[int]] = mapped_column(JSON, default=list)
    moment_refs: Mapped[list[int]] = mapped_column(JSON, default=list)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[ShortFormConceptStatus] = mapped_column(
        enum_column(ShortFormConceptStatus, "short_form_concept_status"),
        default=ShortFormConceptStatus.PROPOSED,
    )
    generation_version: Mapped[str] = mapped_column(String(40))
    overlap_warning: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    clips: Mapped[list["ShortFormClip"]] = relationship(
        cascade="all, delete-orphan", order_by="ShortFormClip.output_order"
    )


class ShortFormClip(Base):
    __tablename__ = "short_form_clips"
    __table_args__ = (
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start", name="ck_short_clip_time"
        ),
        Index("ix_short_clip_concept_order", "concept_id", "output_order", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(
        ForeignKey("short_form_concepts.id", ondelete="CASCADE"), index=True
    )
    moment_id: Mapped[int | None] = mapped_column(
        ForeignKey("interesting_moments.id", ondelete="SET NULL")
    )
    source_start: Mapped[float]
    source_end: Mapped[float]
    output_order: Mapped[int]
    target_output_duration: Mapped[float]
    speed_recommendation: Mapped[float]
    role: Mapped[ShortFormClipRole] = mapped_column(
        enum_column(ShortFormClipRole, "short_form_clip_role")
    )
