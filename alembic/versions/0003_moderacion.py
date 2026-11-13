"""Fase 3: formulario público y moderación.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evento", sa.Column("contacto", sa.String(200)))
    op.add_column("evento", sa.Column("ip_hash", sa.String(64)))
    op.add_column("evento", sa.Column("moderado", sa.DateTime(timezone=True)))
    op.create_index("ix_evento_ip_hash", "evento", ["ip_hash"])


def downgrade() -> None:
    op.drop_index("ix_evento_ip_hash", table_name="evento")
    op.drop_column("evento", "moderado")
    op.drop_column("evento", "ip_hash")
    op.drop_column("evento", "contacto")
