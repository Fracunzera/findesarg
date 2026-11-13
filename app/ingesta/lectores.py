"""Lectores de fuentes: convierten lo bajado (RSS, sitemap, HTML, JSON) en candidatos.

Son funciones puras (texto de entrada -> datos), así se testean sin red.
"""
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import feedparser


@dataclass
class Candidato:
    """Una nota a procesar (todavía sin texto, o con el texto si la fuente ya lo trae)."""

    url: str
    titulo: str | None = None
    fecha_publicacion: datetime | None = None
    texto: str | None = None


@dataclass
class EventoDirecto:
    """Un evento que la fuente ya trae estructurado (no pasa por el LLM)."""

    id_externo: str
    titulo: str
    inicio: datetime
    fin: datetime | None
    hora_confirmada: bool
    descripcion: str | None
    lugar_texto: str | None
    url: str
    extra: dict = field(default_factory=dict)


def limpiar_url(url: str) -> str:
    """Saca el fragmento (#...) y espacios; deja la query (hay sitios que la usan)."""
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def html_a_texto(fragmento: str) -> str:
    t = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", fragmento, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_lib.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


# --- RSS ---------------------------------------------------------------------------------
def leer_rss(contenido: str) -> list[Candidato]:
    feed = feedparser.parse(contenido)
    salida = []
    for e in feed.entries:
        if not e.get("link"):
            continue
        fecha = None
        if e.get("published_parsed") or e.get("updated_parsed"):
            t = e.get("published_parsed") or e.get("updated_parsed")
            fecha = datetime(*t[:6], tzinfo=timezone.utc)
        # El resumen del RSS suele venir cortado: no lo usamos, después se baja la nota completa.
        salida.append(Candidato(url=limpiar_url(e.link), titulo=e.get("title"), fecha_publicacion=fecha))
    return salida


# --- Sitemap de noticias (Google News) ---------------------------------------------------
NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


def _fecha_iso(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def leer_sitemap_noticias(contenido: str) -> list[Candidato]:
    raiz = ET.fromstring(contenido.strip().encode())
    salida = []
    for u in raiz.findall("s:url", NS):
        loc = u.findtext("s:loc", namespaces=NS)
        if not loc:
            continue
        salida.append(
            Candidato(
                url=limpiar_url(loc),
                titulo=u.findtext("news:news/news:title", namespaces=NS),
                fecha_publicacion=_fecha_iso(
                    u.findtext("news:news/news:publication_date", namespaces=NS)
                    or u.findtext("s:lastmod", namespaces=NS)
                ),
            )
        )
    return salida


# --- Listados HTML ----------------------------------------------------------------------
_RE_ENLACE = re.compile(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
_RE_FECHA_URL = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/")


def leer_listado(contenido: str, url_base: str, patron: str) -> list[Candidato]:
    """Saca los enlaces a notas de una página de listado. `patron` es una regex sobre la URL."""
    rx = re.compile(patron)
    vistos: dict[str, Candidato] = {}
    for href, interior in _RE_ENLACE.findall(contenido):
        url = limpiar_url(urljoin(url_base, html_lib.unescape(href)))
        if not rx.search(url):
            continue
        titulo = html_a_texto(interior) or None
        previo = vistos.get(url)
        if previo is None:
            fecha = None
            m = _RE_FECHA_URL.search(url)
            if m:
                fecha = datetime(int(m[1]), int(m[2]), int(m[3]), 12, tzinfo=timezone.utc)
            vistos[url] = Candidato(url=url, titulo=titulo, fecha_publicacion=fecha)
        elif titulo and (not previo.titulo or len(titulo) > len(previo.titulo)):
            previo.titulo = titulo  # la misma nota suele estar enlazada desde la foto y el título
    return list(vistos.values())


# --- API de la Municipalidad de Río Gallegos --------------------------------------------
def leer_muni_noticias(contenido: str, url_nota: str) -> list[Candidato]:
    salida = []
    for n in json.loads(contenido):
        if n.get("is_visible") is False:
            continue
        partes = [n.get("summary") or ""]
        for bloque in n.get("content") or []:
            if isinstance(bloque, dict) and bloque.get("type") == "text":
                partes.append(html_a_texto(bloque.get("content") or ""))
        salida.append(
            Candidato(
                url=url_nota.format(slug=n["slug"]),
                titulo=n.get("title"),
                fecha_publicacion=_fecha_iso(n.get("published_at") or n.get("created_at")),
                texto="\n\n".join(p for p in partes if p).strip() or None,
            )
        )
    return salida


def _resumen(texto: str | None, max_oraciones: int = 2, max_chars: int = 320) -> str | None:
    """Primeras oraciones de la descripción oficial (no copiamos textos largos)."""
    if not texto:
        return None
    t = " ".join(texto.split())
    oraciones = re.split(r"(?<=[.!?])\s+", t)
    r = " ".join(oraciones[:max_oraciones])
    return r if len(r) <= max_chars else r[: max_chars - 1].rsplit(" ", 1)[0] + "…"


def leer_muni_calendario(contenido: str, tz: str, url_sitio: str) -> list[EventoDirecto]:
    zona = ZoneInfo(tz)
    salida = []
    for e in json.loads(contenido):
        inicio = _fecha_iso(e.get("start"))
        if inicio is None:
            continue
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=zona)
        fin = _fecha_iso(e.get("end"))
        if fin is not None and fin.tzinfo is None:
            fin = fin.replace(tzinfo=zona)
        ext = e.get("extendedProps") or {}
        sin_hora = bool(e.get("allDay") or e.get("time_to_be_confirmed") or ext.get("time_to_be_confirmed"))
        if sin_hora:
            inicio = inicio.replace(hour=0, minute=0, second=0)
        salida.append(
            EventoDirecto(
                id_externo=str(e["id"]),
                titulo=" ".join((e.get("title") or "").split()),
                inicio=inicio,
                fin=fin,
                hora_confirmada=not sin_hora,
                descripcion=_resumen(ext.get("descripcion")),
                lugar_texto=(ext.get("lugar") or "").strip() or None,
                # El calendario no tiene página por evento: enlazamos la portada del municipio.
                url=f"{url_sitio.rstrip('/')}/#{e['id']}",
                extra={"calendario": ext.get("calendario")},
            )
        )
    return salida
