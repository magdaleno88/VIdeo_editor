"""add caption plans and final renders

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("narration_alignments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("word_timings", sa.JSON(), nullable=True))

    op.create_table(
        "caption_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("render_asset_id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("narration_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column(
            "target_platform",
            sa.Enum("FACEBOOK_REELS", name="caption_target_platform", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "style_profile",
            sa.Enum("CLEAN", "BOLD", "MINIMAL", name="caption_style_profile", native_enum=False),
            nullable=False,
        ),
        sa.Column("segmentation_strategy", sa.String(length=40), nullable=False),
        sa.Column(
            "timing_method",
            sa.Enum(
                "PROVIDER_ALIGNMENT",
                "SENTENCE_ALIGNMENT",
                "ESTIMATED_ALIGNMENT",
                name="caption_timing_method",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "emphasis_mode",
            sa.Enum("NONE", "PHRASE", "WORD", name="caption_emphasis_mode", native_enum=False),
            nullable=False,
        ),
        sa.Column("safe_area", sa.JSON(), nullable=False),
        sa.Column("planner_version", sa.String(length=40), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["narration_id"], ["narration_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["render_asset_id"], ["render_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["script_id"], ["script_drafts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_caption_plan_render_created", "caption_plans", ["render_asset_id", "created_at"]
    )
    op.create_index("ix_caption_plan_reuse", "caption_plans", ["cache_key"])
    for column in ("render_asset_id", "script_id", "narration_id", "cache_key"):
        op.create_index(f"ix_caption_plans_{column}", "caption_plans", [column])

    op.create_table(
        "caption_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caption_plan_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("spoken_text", sa.Text(), nullable=False),
        sa.Column("sentence_id", sa.Integer(), nullable=False),
        sa.Column("beat_id", sa.Integer(), nullable=False),
        sa.Column(
            "timing_method",
            sa.Enum(
                "PROVIDER_ALIGNMENT",
                "SENTENCE_ALIGNMENT",
                "ESTIMATED_ALIGNMENT",
                name="caption_item_timing_method",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "position_name",
            sa.Enum("UPPER", "CENTER", "LOWER", name="caption_position", native_enum=False),
            nullable=False,
        ),
        sa.Column("style", sa.JSON(), nullable=False),
        sa.Column("emphasis_spans", sa.JSON(), nullable=False),
        sa.Column("characters_per_second", sa.Float(), nullable=False),
        sa.Column("words_per_minute", sa.Float(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.CheckConstraint("end_seconds > start_seconds", name="ck_caption_item_interval"),
        sa.ForeignKeyConstraint(["beat_id"], ["script_beats.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["caption_plan_id"], ["caption_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sentence_id"], ["script_sentences.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("caption_plan_id", "position", name="uq_caption_item_position"),
    )
    for column in ("caption_plan_id", "sentence_id", "beat_id"):
        op.create_index(f"ix_caption_items_{column}", "caption_items", [column])

    op.create_table(
        "graphic_overlays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caption_plan_id", sa.Integer(), nullable=False),
        sa.Column(
            "overlay_type",
            sa.Enum(
                "HOOK_TEXT",
                "INFO_LABEL",
                "BRANDING",
                name="graphic_overlay_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column(
            "position_name",
            sa.Enum("UPPER", "CENTER", "LOWER", name="graphic_overlay_position", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "style_profile",
            sa.Enum("CLEAN", "BOLD", "MINIMAL", name="graphic_overlay_style", native_enum=False),
            nullable=False,
        ),
        sa.Column("claim_ids", sa.JSON(), nullable=False),
        sa.Column("beat_ids", sa.JSON(), nullable=False),
        sa.Column("z_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("end_seconds > start_seconds", name="ck_graphic_overlay_interval"),
        sa.ForeignKeyConstraint(["caption_plan_id"], ["caption_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graphic_overlays_caption_plan_id", "graphic_overlays", ["caption_plan_id"])

    op.create_table(
        "final_render_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caption_plan_id", sa.Integer(), nullable=False),
        sa.Column("raw_render_id", sa.Integer(), nullable=False),
        sa.Column("output_path", sa.String(length=500), nullable=True, unique=True),
        sa.Column("ass_path", sa.String(length=500), nullable=True, unique=True),
        sa.Column("srt_path", sa.String(length=500), nullable=True, unique=True),
        sa.Column("preview_path", sa.String(length=500), nullable=True, unique=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("video_codec", sa.String(length=80), nullable=True),
        sa.Column("audio_codec", sa.String(length=80), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("render_version", sa.String(length=40), nullable=False),
        sa.Column("subtitle_renderer_version", sa.String(length=40), nullable=False),
        sa.Column("ffmpeg_version", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "RENDERING",
                "VALIDATED",
                "NEEDS_REVIEW",
                "APPROVED",
                "REJECTED",
                "FAILED",
                name="final_render_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("process_exit_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["caption_plan_id"], ["caption_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["raw_render_id"], ["render_assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_final_render_plan_created", "final_render_assets", ["caption_plan_id", "created_at"]
    )
    op.create_index("ix_final_render_reuse", "final_render_assets", ["cache_key"])
    for column in ("caption_plan_id", "raw_render_id", "cache_key", "status"):
        op.create_index(f"ix_final_render_assets_{column}", "final_render_assets", [column])

    op.create_table(
        "final_render_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("final_render_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reviewer", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["final_render_id"], ["final_render_assets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_final_render_reviews_final_render_id", "final_render_reviews", ["final_render_id"]
    )


def downgrade() -> None:
    op.drop_table("final_render_reviews")
    op.drop_table("final_render_assets")
    op.drop_table("graphic_overlays")
    op.drop_table("caption_items")
    op.drop_table("caption_plans")
    with op.batch_alter_table("narration_alignments", schema=None) as batch_op:
        batch_op.drop_column("word_timings")
