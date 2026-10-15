"""Allow clearing terminal task logs without deleting linked business records.

Revision ID: d49a25e8b013
Revises: c38f14d7a902
"""

import sqlalchemy as sa
from alembic import op

revision = "d49a25e8b013"
down_revision = "c38f14d7a902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("logs_cleared_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "logs_cleared_at")
