"""Add versioned score evaluations.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("scorer_version", sa.String(length=40), nullable=False),
        sa.Column("ai_provider", sa.String(length=60), nullable=True),
        sa.Column("ai_model", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("ratings", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("overall_confidence", sa.Float(), nullable=True),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)",
            name="ck_evaluation_confidence",
        ),
        sa.CheckConstraint(
            "total_score >= 0 AND total_score <= 100",
            name="ck_evaluation_total_score",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["video_candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("score_evaluations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_score_evaluation_candidate_created",
            ["candidate_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_score_evaluation_reuse",
            [
                "candidate_id",
                "method",
                "ai_provider",
                "ai_model",
                "prompt_version",
                "scorer_version",
            ],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_score_evaluations_candidate_id"),
            ["candidate_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_score_evaluations_method"), ["method"], unique=False)
    _backfill_current_scores()


def _backfill_current_scores() -> None:
    connection = op.get_bind()
    candidates = sa.table(
        "video_candidates",
        sa.column("id", sa.Integer()),
        sa.column("score_details", sa.JSON()),
        sa.column("total_score", sa.Float()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    evaluations = sa.table(
        "score_evaluations",
        sa.column("candidate_id", sa.Integer()),
        sa.column("method", sa.String()),
        sa.column("scorer_version", sa.String()),
        sa.column("weights", sa.JSON()),
        sa.column("ratings", sa.JSON()),
        sa.column("total_score", sa.Float()),
        sa.column("overall_confidence", sa.Float()),
        sa.column("analysis", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = connection.execute(
        sa.select(candidates).where(candidates.c.score_details.is_not(None))
    ).mappings()
    for row in rows:
        details = row["score_details"]
        connection.execute(
            evaluations.insert().values(
                candidate_id=row["id"],
                method=details["method"],
                scorer_version=details.get("version", "1.0"),
                weights=details["weights"],
                ratings=details["inputs"],
                total_score=row["total_score"],
                overall_confidence=None,
                analysis=None,
                created_at=row["updated_at"],
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("score_evaluations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_score_evaluations_method"))
        batch_op.drop_index(batch_op.f("ix_score_evaluations_candidate_id"))
        batch_op.drop_index("ix_score_evaluation_reuse")
        batch_op.drop_index("ix_score_evaluation_candidate_created")
    op.drop_table("score_evaluations")
