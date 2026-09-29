"""add long-form sources and short-form concepts

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    with op.batch_alter_table("video_edit_plans") as batch:
        batch.alter_column(
            "composition",
            existing_type=enum(
                "CENTER_CROP", "FIT", "BLURRED_BACKGROUND", name="composition_strategy"
            ),
            type_=enum(
                "CENTER_CROP",
                "FIT",
                "BLURRED_BACKGROUND",
                "ROI_AWARE",
                name="composition_strategy",
            ),
            existing_nullable=False,
        )
        batch.alter_column(
            "source_audio_policy",
            existing_type=enum("MUTED", name="source_audio_policy"),
            type_=enum("MUTED", "AMBIENT_REDUCED", name="source_audio_policy"),
            existing_nullable=False,
        )
    with op.batch_alter_table("edit_segments") as batch:
        batch.alter_column(
            "composition",
            existing_type=enum(
                "CENTER_CROP",
                "FIT",
                "BLURRED_BACKGROUND",
                name="edit_segment_composition_strategy",
            ),
            type_=enum(
                "CENTER_CROP",
                "FIT",
                "BLURRED_BACKGROUND",
                "ROI_AWARE",
                name="edit_segment_composition_strategy",
            ),
            existing_nullable=False,
        )
    op.create_table(
        "long_form_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "source_type",
            enum(
                "LOCAL_UPLOAD",
                "LOCAL_IMPORT",
                "STOCK_PROVIDER",
                "AUTHORIZED_REMOTE",
                name="long_form_source_type",
            ),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("relative_path", sa.String(500), nullable=False, unique=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("fps", sa.Float(), nullable=False),
        sa.Column("video_codec", sa.String(80), nullable=False),
        sa.Column("audio_codec", sa.String(80)),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("container", sa.String(200), nullable=False),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            enum(
                "INGESTED",
                "PREPROCESSED",
                "ANALYZED",
                "AI_ANALYSIS_BLOCKED",
                "INVALID",
                name="long_form_source_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "analysis_status",
            enum("PLANNED", "COMPLETED", "BLOCKED", "FAILED", name="source_analysis_status"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("duration_seconds > 0", name="ck_long_source_duration"),
        sa.CheckConstraint("size_bytes > 0", name="ck_long_source_size"),
    )
    op.create_index(
        "ix_long_form_sources_checksum_sha256",
        "long_form_sources",
        ["checksum_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_long_form_source_status_created", "long_form_sources", ["status", "created_at"]
    )
    op.create_table(
        "source_rights",
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "rights_status",
            enum(
                "VERIFIED",
                "UNKNOWN",
                "RESTRICTED",
                "MANUAL_REVIEW_REQUIRED",
                name="source_rights_status",
            ),
            nullable=False,
        ),
        sa.Column("license_name", sa.String(200)),
        sa.Column("commercial_use_allowed", sa.Boolean()),
        sa.Column("derivative_works_allowed", sa.Boolean()),
        sa.Column("attribution_required", sa.Boolean()),
        sa.Column("attribution_text", sa.Text()),
        sa.Column("evidence_reference", sa.Text()),
        sa.Column("reviewed_by", sa.String(200)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text(), nullable=False),
    )
    op.create_table(
        "source_scenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("representative_frame_path", sa.String(500)),
        sa.Column("technical_metadata", sa.JSON(), nullable=False),
        sa.Column("heuristic_score", sa.Float()),
        sa.CheckConstraint(
            "start_seconds >= 0 AND end_seconds > start_seconds", name="ck_scene_time"
        ),
    )
    op.create_index("ix_source_scenes_source_id", "source_scenes", ["source_id"])
    op.create_index(
        "ix_source_scene_source_position", "source_scenes", ["source_id", "position"], unique=True
    )
    op.create_table(
        "source_transcripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_transcripts_source_id", "source_transcripts", ["source_id"])
    op.create_table(
        "source_transcript_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "transcript_id",
            sa.Integer(),
            sa.ForeignKey("source_transcripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("language", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "start_seconds >= 0 AND end_seconds > start_seconds", name="ck_transcript_time"
        ),
    )
    op.create_index(
        "ix_source_transcript_segments_transcript_id",
        "source_transcript_segments",
        ["transcript_id"],
    )
    op.create_table(
        "source_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            enum("PLANNED", "COMPLETED", "BLOCKED", "FAILED", name="source_analysis_run_status"),
            nullable=False,
        ),
        sa.Column(
            "profile",
            enum("ECONOMY", "BALANCED", "QUALITY", name="source_analysis_profile"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("detected_process", sa.String(300)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_analyses_source_id", "source_analyses", ["source_id"])
    op.create_table(
        "process_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("source_analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("visual_description", sa.Text(), nullable=False),
        sa.Column("transcript_context", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("stage_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "start_seconds >= 0 AND end_seconds > start_seconds", name="ck_stage_time"
        ),
    )
    op.create_index("ix_process_stages_source_id", "process_stages", ["source_id"])
    op.create_index("ix_process_stages_analysis_id", "process_stages", ["analysis_id"])
    op.create_table(
        "interesting_moments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("source_analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stage_id", sa.Integer(), sa.ForeignKey("process_stages.id", ondelete="SET NULL")
        ),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("proposed_start_seconds", sa.Float(), nullable=False),
        sa.Column("proposed_end_seconds", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("visual_interest_score", sa.Float(), nullable=False),
        sa.Column("educational_value", sa.Float(), nullable=False),
        sa.Column("transformation_score", sa.Float(), nullable=False),
        sa.Column("motion_score", sa.Float(), nullable=False),
        sa.Column("machinery_score", sa.Float(), nullable=False),
        sa.Column("hook_score", sa.Float(), nullable=False),
        sa.Column("loop_potential", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "start_seconds >= 0 AND end_seconds > start_seconds", name="ck_moment_time"
        ),
    )
    op.create_index("ix_interesting_moments_source_id", "interesting_moments", ["source_id"])
    op.create_index("ix_interesting_moments_analysis_id", "interesting_moments", ["analysis_id"])
    op.create_index("ix_interesting_moments_stage_id", "interesting_moments", ["stage_id"])
    op.create_table(
        "short_form_concepts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("long_form_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("source_analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("video_candidates.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("angle", sa.Text(), nullable=False),
        sa.Column("target_duration", sa.Float(), nullable=False),
        sa.Column("hook_candidate", sa.Text(), nullable=False),
        sa.Column("stage_refs", sa.JSON(), nullable=False),
        sa.Column("moment_refs", sa.JSON(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            enum("PROPOSED", "APPROVED", "REJECTED", name="short_form_concept_status"),
            nullable=False,
        ),
        sa.Column("generation_version", sa.String(40), nullable=False),
        sa.Column("overlap_warning", sa.Text()),
        sa.Column("reviewed_by", sa.String(200)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_short_form_concepts_source_id", "short_form_concepts", ["source_id"])
    op.create_index("ix_short_form_concepts_analysis_id", "short_form_concepts", ["analysis_id"])
    op.create_table(
        "short_form_clips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "concept_id",
            sa.Integer(),
            sa.ForeignKey("short_form_concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "moment_id", sa.Integer(), sa.ForeignKey("interesting_moments.id", ondelete="SET NULL")
        ),
        sa.Column("source_start", sa.Float(), nullable=False),
        sa.Column("source_end", sa.Float(), nullable=False),
        sa.Column("output_order", sa.Integer(), nullable=False),
        sa.Column("target_output_duration", sa.Float(), nullable=False),
        sa.Column("speed_recommendation", sa.Float(), nullable=False),
        sa.Column(
            "role",
            enum(
                "HOOK",
                "CONTEXT",
                "PROCESS",
                "TRANSFORMATION",
                "RESULT",
                name="short_form_clip_role",
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_start >= 0 AND source_end > source_start", name="ck_short_clip_time"
        ),
    )
    op.create_index("ix_short_form_clips_concept_id", "short_form_clips", ["concept_id"])
    op.create_index(
        "ix_short_clip_concept_order",
        "short_form_clips",
        ["concept_id", "output_order"],
        unique=True,
    )


def downgrade() -> None:
    for table in (
        "short_form_clips",
        "short_form_concepts",
        "interesting_moments",
        "process_stages",
        "source_analyses",
        "source_transcript_segments",
        "source_transcripts",
        "source_scenes",
        "source_rights",
        "long_form_sources",
    ):
        op.drop_table(table)
    with op.batch_alter_table("edit_segments") as batch:
        batch.alter_column(
            "composition",
            existing_type=enum(
                "CENTER_CROP",
                "FIT",
                "BLURRED_BACKGROUND",
                "ROI_AWARE",
                name="edit_segment_composition_strategy",
            ),
            type_=enum(
                "CENTER_CROP", "FIT", "BLURRED_BACKGROUND", name="edit_segment_composition_strategy"
            ),
            existing_nullable=False,
        )
    with op.batch_alter_table("video_edit_plans") as batch:
        batch.alter_column(
            "source_audio_policy",
            existing_type=enum("MUTED", "AMBIENT_REDUCED", name="source_audio_policy"),
            type_=enum("MUTED", name="source_audio_policy"),
            existing_nullable=False,
        )
        batch.alter_column(
            "composition",
            existing_type=enum(
                "CENTER_CROP",
                "FIT",
                "BLURRED_BACKGROUND",
                "ROI_AWARE",
                name="composition_strategy",
            ),
            type_=enum("CENTER_CROP", "FIT", "BLURRED_BACKGROUND", name="composition_strategy"),
            existing_nullable=False,
        )
