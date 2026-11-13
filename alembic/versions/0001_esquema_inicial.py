"""Esquema inicial: ciudad, lugar, fuente, item_crudo y evento.

Revision ID: 0001
Revises: 
Create Date: 2026-11-12 23:11:45.503926
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table('ciudad',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('nombre', sa.String(length=120), nullable=False),
    sa.Column('provincia', sa.String(length=120), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('bbox', geoalchemy2.types.Geometry(geometry_type='POLYGON', srid=4326, from_text='ST_GeomFromEWKT', name='geometry', nullable=False), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('fuente',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ciudad_id', sa.Integer(), nullable=False),
    sa.Column('clave', sa.String(length=80), nullable=False),
    sa.Column('nombre', sa.String(length=200), nullable=False),
    sa.Column('tipo', sa.String(length=32), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('activa', sa.Boolean(), nullable=False),
    sa.Column('ultima_corrida', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ultimo_ok', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ultimo_error', sa.Text(), nullable=True),
    sa.Column('ultimos_items', sa.Integer(), nullable=True),
    sa.CheckConstraint("tipo IN ('rss', 'html', 'telegram', 'formulario', 'instagram_api')", name='fuente_tipo_valido'),
    sa.ForeignKeyConstraint(['ciudad_id'], ['ciudad.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('clave')
    )
    op.create_index(op.f('ix_fuente_ciudad_id'), 'fuente', ['ciudad_id'], unique=False)
    op.create_table('lugar',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ciudad_id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=200), nullable=True),
    sa.Column('tipo', sa.String(length=32), nullable=False),
    sa.Column('geom', geoalchemy2.types.Geometry(geometry_type='POINT', srid=4326, from_text='ST_GeomFromEWKT', name='geometry', nullable=False), nullable=False),
    sa.Column('direccion', sa.String(length=300), nullable=True),
    sa.Column('horario_osm', sa.Text(), nullable=True),
    sa.Column('origen', sa.String(length=16), nullable=False),
    sa.Column('osm_id', sa.String(length=32), nullable=True),
    sa.Column('etiquetas_osm', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('creado', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actualizado', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("origen IN ('osm', 'manual')", name='lugar_origen_valido'),
    sa.CheckConstraint("tipo IN ('plaza', 'parque', 'juegos', 'jardin', 'centro_cultural', 'bar', 'teatro', 'otro')", name='lugar_tipo_valido'),
    sa.ForeignKeyConstraint(['ciudad_id'], ['ciudad.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ciudad_id', 'osm_id', name='lugar_osm_unico')
    )
    op.create_index(op.f('ix_lugar_ciudad_id'), 'lugar', ['ciudad_id'], unique=False)
    op.create_table('item_crudo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fuente_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('titulo', sa.Text(), nullable=True),
    sa.Column('texto', sa.Text(), nullable=True),
    sa.Column('fecha_publicacion', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hash_contenido', sa.String(length=64), nullable=True),
    sa.Column('paso_prefiltro', sa.Boolean(), nullable=True),
    sa.Column('procesado', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('creado', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['fuente_id'], ['fuente.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fuente_id', 'url', name='item_crudo_url_unica')
    )
    op.create_index(op.f('ix_item_crudo_fuente_id'), 'item_crudo', ['fuente_id'], unique=False)
    op.create_table('evento',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ciudad_id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=200), nullable=True),
    sa.Column('titulo', sa.String(length=300), nullable=False),
    sa.Column('descripcion', sa.Text(), nullable=True),
    sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fin', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hora_confirmada', sa.Boolean(), nullable=False),
    sa.Column('lugar_id', sa.Integer(), nullable=True),
    sa.Column('lugar_texto', sa.String(length=300), nullable=True),
    sa.Column('precio_texto', sa.String(length=200), nullable=True),
    sa.Column('gratis', sa.Boolean(), nullable=True),
    sa.Column('categoria', sa.String(length=32), nullable=False),
    sa.Column('url_origen', sa.Text(), nullable=True),
    sa.Column('urls_alternativas', sa.ARRAY(sa.Text()), nullable=False),
    sa.Column('fuente_id', sa.Integer(), nullable=True),
    sa.Column('item_crudo_id', sa.Integer(), nullable=True),
    sa.Column('estado', sa.String(length=16), nullable=False),
    sa.Column('confianza', sa.Float(), nullable=False),
    sa.Column('hash_dedup', sa.String(length=64), nullable=True),
    sa.Column('creado', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actualizado', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("categoria IN ('musica', 'teatro', 'cine', 'feria', 'deporte', 'taller', 'muestra', 'infantil', 'gastronomia', 'otro')", name='evento_categoria_valida'),
    sa.CheckConstraint("estado IN ('pendiente', 'publicado', 'rechazado')", name='evento_estado_valido'),
    sa.CheckConstraint('confianza >= 0 AND confianza <= 1', name='evento_confianza_rango'),
    sa.ForeignKeyConstraint(['ciudad_id'], ['ciudad.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['fuente_id'], ['fuente.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_crudo_id'], ['item_crudo.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lugar_id'], ['lugar.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_index(op.f('ix_evento_ciudad_id'), 'evento', ['ciudad_id'], unique=False)
    op.create_index(op.f('ix_evento_estado'), 'evento', ['estado'], unique=False)
    op.create_index(op.f('ix_evento_hash_dedup'), 'evento', ['hash_dedup'], unique=False)
    op.create_index(op.f('ix_evento_inicio'), 'evento', ['inicio'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_evento_inicio'), table_name='evento')
    op.drop_index(op.f('ix_evento_hash_dedup'), table_name='evento')
    op.drop_index(op.f('ix_evento_estado'), table_name='evento')
    op.drop_index(op.f('ix_evento_ciudad_id'), table_name='evento')
    op.drop_table('evento')
    op.drop_index(op.f('ix_item_crudo_fuente_id'), table_name='item_crudo')
    op.drop_table('item_crudo')
    op.drop_index(op.f('ix_lugar_ciudad_id'), table_name='lugar')
    op.drop_table('lugar')
    op.drop_index(op.f('ix_fuente_ciudad_id'), table_name='fuente')
    op.drop_table('fuente')
    op.drop_table('ciudad')
