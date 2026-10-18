"""Add opt-in card bill tracking, preserving existing cards.

Revision ID: a72d58b1e346
Revises: f61c47a0d235
"""

import sqlalchemy as sa
from alembic import op

revision = "a72d58b1e346"
down_revision = "f61c47a0d235"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("track_bills", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("cards", "track_bills")
