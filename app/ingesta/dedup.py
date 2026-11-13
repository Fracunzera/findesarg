"""Deduplicación de eventos.

Dos notas de medios distintos suelen hablar del mismo recital con títulos parecidos
("Abel Pintos en Río Gallegos" / "Llega Abel Pintos al Boxing"). Normalizamos título,
fecha y lugar, y comparamos con rapidfuzz contra los eventos de ±1 día.
"""
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from rapidfuzz import fuzz

STOPWORDS = {
    "a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los", "para", "por", "se",
    "un", "una", "y", "e", "o", "u", "su", "sus", "que", "este", "esta", "llega", "llegan",
    "vuelve", "vuelven", "presenta", "presentan", "rio", "gallegos", "nueva", "nuevo", "edicion",
}

UMBRAL_TITULO = 88          # con esto solo ya es el mismo evento
UMBRAL_TITULO_CON_LUGAR = 72  # si además coincide el lugar
UMBRAL_LUGAR = 80


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9ñ ]+", " ", t)
    palabras = [p for p in t.split() if p not in STOPWORDS]
    return " ".join(palabras)


@dataclass
class DatosDedup:
    titulo: str
    inicio: datetime
    lugar: str | None = None


def hash_dedup(d: DatosDedup, tz) -> str:
    fecha = d.inicio.astimezone(tz).date().isoformat()
    base = f"{normalizar(d.titulo)}|{fecha}|{normalizar(d.lugar)}"
    return hashlib.sha256(base.encode()).hexdigest()


def puntaje(a: DatosDedup, b: DatosDedup) -> tuple[float, float | None]:
    """(similitud de título, similitud de lugar o None si falta alguno)."""
    t = fuzz.token_set_ratio(normalizar(a.titulo), normalizar(b.titulo))
    la, lb = normalizar(a.lugar), normalizar(b.lugar)
    l = fuzz.token_set_ratio(la, lb) if la and lb else None
    return t, l


def es_mismo_evento(a: DatosDedup, b: DatosDedup) -> bool:
    if abs((a.inicio - b.inicio).total_seconds()) > 36 * 3600:
        return False
    t, l = puntaje(a, b)
    if l is not None and l < 50:
        return False  # mismo nombre en lugares claramente distintos (ej. "Feria" en dos barrios)
    if t >= UMBRAL_TITULO:
        return True
    return t >= UMBRAL_TITULO_CON_LUGAR and l is not None and l >= UMBRAL_LUGAR


CAMPOS_COMPLETITUD = ("descripcion", "fin", "lugar_texto", "lugar_id", "precio_texto")


def completitud(ev) -> float:
    """Cuán completo es un registro (sirve para quedarse con el mejor)."""
    p = sum(1 for c in CAMPOS_COMPLETITUD if getattr(ev, c, None))
    p += 1 if getattr(ev, "gratis", None) is not None else 0
    p += 2 if getattr(ev, "hora_confirmada", False) else 0
    p += float(getattr(ev, "confianza", 0) or 0)
    return p
