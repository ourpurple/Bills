"""Add optional card credit limit and recurring annual fee details.

Revision ID: e50b36f9c124
Revises: d49a25e8b013
"""

import sqlalchemy as sa
from alembic import op

revision = "e50b36f9c124"
down_revision = "d49a25e8b013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("credit_limit_minor", sa.Integer(), nullable=True))
    op.add_column("cards", sa.Column("annual_fee_month", sa.Integer(), nullable=True))
    op.add_column("cards", sa.Column("annual_fee_day", sa.Integer(), nullable=True))
    op.add_column("cards", sa.Column("annual_fee_benefits", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cards", "annual_fee_benefits")
    op.drop_column("cards", "annual_fee_day")
    op.drop_column("cards", "annual_fee_month")
    op.drop_column("cards", "credit_limit_minor")
