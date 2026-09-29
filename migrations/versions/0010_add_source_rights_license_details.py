"""add source rights license details

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    with op.batch_alter_table("source_rights") as batch:
        batch.add_column(
            sa.Column(
                "license_preset",
                enum(
                    "CC0",
                    "PUBLIC_DOMAIN",
                    "CC_BY",
                    "CC_BY_SA",
                    "CUSTOM",
                    name="source_license_preset",
                ),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("license_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("creator", sa.String(500), nullable=True))
        batch.add_column(sa.Column("share_alike_required", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_rights") as batch:
        batch.drop_constraint("source_license_preset", type_="check")
        batch.drop_column("share_alike_required")
        batch.drop_column("creator")
        batch.drop_column("license_url")
        batch.drop_column("license_preset")
