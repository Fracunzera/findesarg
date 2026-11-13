"""Modelo de datos.

Los "enums" se guardan como texto con CHECK en la base: son más fáciles de
ampliar con una migración que los ENUM nativos de Postgres.
"""
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TIPOS_LUGAR = ("plaza", "parque", "juegos", "jardin", "centro_cultural", "bar", "teatro", "otro")
ORIGENES_LUGAR = ("osm", "manual")
ESTADOS_EVENTO = ("pendiente", "publicado", "rechazado")
CATEGORIAS = (
    "musica", "teatro", "cine", "feria", "deporte", "taller",
    "muestra", "infantil", "gastronomia", "otro",
)
# "api" = JSON público de un sitio (por ejemplo, el calendario de la Municipalidad)
TIPOS_FUENTE = ("rss", "html", "api", "telegram", "formulario", "instagram_api")


def _check_en(columna: str, valores: tuple[str, ...]) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    return f"{columna} IN ({lista})"


class Base(DeclarativeBase):
    pass


class Ciudad(Base):
    __tablename__ = "ciudad"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)  # para las URLs: /rio-gallegos
    nombre: Mapped[str] = mapped_column(String(120))
    provincia: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64))
    bbox = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)

    lugares: Mapped[list["Lugar"]] = relationship(back_populates="ciudad")


class Lugar(Base):
    __tablename__ = "lugar"
    __table_args__ = (
        CheckConstraint(_check_en("tipo", TIPOS_LUGAR), name="lugar_tipo_valido"),
        CheckConstraint(_check_en("origen", ORIGENES_LUGAR), name="lugar_origen_valido"),
        UniqueConstraint("ciudad_id", "osm_id", name="lugar_osm_unico"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ciudad_id: Mapped[int] = mapped_column(ForeignKey("ciudad.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[str | None] = mapped_column(String(200))  # en OSM hay muchos sin nombre
    tipo: Mapped[str] = mapped_column(String(32))
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(300))
    horario_osm: Mapped[str | None] = mapped_column(Text)  # opening_hours crudo, tal cual
    origen: Mapped[str] = mapped_column(String(16), default="manual")
    osm_id: Mapped[str | None] = mapped_column(String(32))  # ej. "way/123456"
    etiquetas_osm: Mapped[dict | None] = mapped_column(JSONB)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ciudad: Mapped[Ciudad] = relationship(back_populates="lugares")


class Fuente(Base):
    __tablename__ = "fuente"
    __table_args__ = (CheckConstraint(_check_en("tipo", TIPOS_FUENTE), name="fuente_tipo_valido"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ciudad_id: Mapped[int] = mapped_column(ForeignKey("ciudad.id", ondelete="CASCADE"), index=True)
    clave: Mapped[str] = mapped_column(String(80), unique=True)  # id estable desde fuentes.yaml
    nombre: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    ultima_corrida: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_error: Mapped[str | None] = mapped_column(Text)
    ultimos_items: Mapped[int | None] = mapped_column(Integer)


class ItemCrudo(Base):
    __tablename__ = "item_crudo"
    __table_args__ = (UniqueConstraint("fuente_id", "url", name="item_crudo_url_unica"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fuente_id: Mapped[int] = mapped_column(ForeignKey("fuente.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    titulo: Mapped[str | None] = mapped_column(Text)
    texto: Mapped[str | None] = mapped_column(Text)
    fecha_publicacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hash_contenido: Mapped[str | None] = mapped_column(String(64))
    paso_prefiltro: Mapped[bool | None] = mapped_column(Boolean)
    procesado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraccion: Mapped[dict | None] = mapped_column(JSONB)  # salida cruda del LLM, para auditar
    intentos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CorridaFuente(Base):
    """Una ejecución de la ingesta de una fuente (para el panel de salud)."""

    __tablename__ = "corrida_fuente"

    id: Mapped[int] = mapped_column(primary_key=True)
    fuente_id: Mapped[int] = mapped_column(ForeignKey("fuente.id", ondelete="CASCADE"), index=True)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    items_vistos: Mapped[int] = mapped_column(Integer, default=0)
    items_nuevos: Mapped[int] = mapped_column(Integer, default=0)
    items_prefiltro: Mapped[int] = mapped_column(Integer, default=0)
    items_extraidos: Mapped[int] = mapped_column(Integer, default=0)
    eventos_nuevos: Mapped[int] = mapped_column(Integer, default=0)
    eventos_fusionados: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    avisos: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)


class Evento(Base):
    __tablename__ = "evento"
    __table_args__ = (
        CheckConstraint(_check_en("estado", ESTADOS_EVENTO), name="evento_estado_valido"),
        CheckConstraint(_check_en("categoria", CATEGORIAS), name="evento_categoria_valida"),
        CheckConstraint("confianza >= 0 AND confianza <= 1", name="evento_confianza_rango"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ciudad_id: Mapped[int] = mapped_column(ForeignKey("ciudad.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str | None] = mapped_column(String(200), unique=True)
    titulo: Mapped[str] = mapped_column(String(300))
    descripcion: Mapped[str | None] = mapped_column(Text)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hora_confirmada: Mapped[bool] = mapped_column(Boolean, default=False)
    lugar_id: Mapped[int | None] = mapped_column(ForeignKey("lugar.id", ondelete="SET NULL"))
    lugar_texto: Mapped[str | None] = mapped_column(String(300))
    precio_texto: Mapped[str | None] = mapped_column(String(200))
    gratis: Mapped[bool | None] = mapped_column(Boolean)
    categoria: Mapped[str] = mapped_column(String(32), default="otro")
    url_origen: Mapped[str | None] = mapped_column(Text)
    urls_alternativas: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # dedup
    fuente_id: Mapped[int | None] = mapped_column(ForeignKey("fuente.id", ondelete="SET NULL"))
    item_crudo_id: Mapped[int | None] = mapped_column(ForeignKey("item_crudo.id", ondelete="SET NULL"))
    estado: Mapped[str] = mapped_column(String(16), default="pendiente", index=True)
    confianza: Mapped[float] = mapped_column(Float, default=0.0)
    hash_dedup: Mapped[str | None] = mapped_column(String(64), index=True)
    # Formulario público: contacto del organizador (solo lo ve el admin) y hash de la IP
    # (para el límite de envíos; nunca guardamos la IP en claro).
    contacto: Mapped[str | None] = mapped_column(String(200))
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    moderado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
