"""Add card identity and encrypted private card fields.

Revision ID: f61c47a0d235
Revises: e50b36f9c124
"""

import sqlalchemy as sa
from alembic import op

revision = "f61c47a0d235"
down_revision = "e50b36f9c124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("card_number_ciphertext", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("expiry", sa.String(5), nullable=True))
    op.add_column("cards", sa.Column("cvv_ciphertext", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("card_type", sa.String(8), nullable=True))
    op.add_column("cards", sa.Column("card_network", sa.String(16), nullable=True))


def downgrade() -> None:
    for name in ("card_network", "card_type", "cvv_ciphertext", "expiry", "card_number_ciphertext"):
        op.drop_column("cards", name)
