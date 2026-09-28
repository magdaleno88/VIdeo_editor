"""add pilot batches and quality reviews

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pilot_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_pilot_batches_slug", "pilot_batches", ["slug"], unique=True)

    op.create_table(
        "pilot_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["pilot_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["video_candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "candidate_id", name="uq_pilot_run_batch_candidate"),
    )
    op.create_index("ix_pilot_runs_batch_id", "pilot_runs", ["batch_id"])
    op.create_index("ix_pilot_runs_candidate_id", "pilot_runs", ["candidate_id"])
    op.create_index("ix_pilot_run_batch_created", "pilot_runs", ["batch_id", "created_at"])

    score_checks = (
        "visual_relevance",
        "pacing",
        "crop_quality",
        "narration_quality",
        "caption_readability",
        "hook_strength",
        "audio_sync",
        "overall_readiness",
    )
    op.create_table(
        "final_render_quality_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("final_render_id", sa.Integer(), nullable=False),
        sa.Column("reviewer", sa.String(length=200), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in score_checks),
        sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("rejection_categories", sa.JSON(), nullable=False),
        sa.Column(
            "recommended_revision_stage",
            sa.Enum(
                "RESEARCH",
                "SCRIPT",
                "NARRATION",
                "RENDER_PLAN",
                "CAPTION_PLAN",
                "FINAL_REVIEW",
                name="quality_revision_stage",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.CheckConstraint(f"{name} >= 1 AND {name} <= 5", name=f"ck_quality_{name}")
            for name in score_checks
        ),
        sa.ForeignKeyConstraint(
            ["final_render_id"], ["final_render_assets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_final_render_quality_reviews_final_render_id",
        "final_render_quality_reviews",
        ["final_render_id"],
    )


def downgrade() -> None:
    op.drop_table("final_render_quality_reviews")
    op.drop_table("pilot_runs")
    op.drop_table("pilot_batches")
