"""Fase 2: ingesta (tipo de fuente "api", corridas y auditoría de extracción).

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fuente_tipo_valido", "fuente", type_="check")
    op.create_check_constraint(
        "fuente_tipo_valido", "fuente",
        "tipo IN ('rss', 'html', 'api', 'telegram', 'formulario', 'instagram_api')",
    )
    op.add_column("item_crudo", sa.Column("extraccion", postgresql.JSONB(), nullable=True))
    op.add_column("item_crudo", sa.Column("intentos", sa.Integer(), server_default="0", nullable=False))
    op.create_table(
        "corrida_fuente",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fuente_id", sa.Integer(), sa.ForeignKey("fuente.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True)),
        sa.Column("ok", sa.Boolean()),
        sa.Column("items_vistos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_nuevos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_prefiltro", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_extraidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eventos_nuevos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eventos_fusionados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("avisos", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.create_index("ix_corrida_fuente_fuente_id", "corrida_fuente", ["fuente_id"])


def downgrade() -> None:
    op.drop_index("ix_corrida_fuente_fuente_id", table_name="corrida_fuente")
    op.drop_table("corrida_fuente")
    op.drop_column("item_crudo", "intentos")
    op.drop_column("item_crudo", "extraccion")
    op.drop_constraint("fuente_tipo_valido", "fuente", type_="check")
    op.create_check_constraint(
        "fuente_tipo_valido", "fuente",
        "tipo IN ('rss', 'html', 'telegram', 'formulario', 'instagram_api')",
    )
