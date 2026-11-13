"""Agenda pública: hoy, el finde, los próximos 7 días y más adelante.

Solo se muestran eventos **publicados**. Cada evento aparece una sola vez, en el primer
grupo que le corresponde. Un evento que ya empezó pero sigue en curso (una muestra de
varios días, una feria que arrancó a la mañana) cuenta como "hoy".
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Ciudad, Evento, Fuente, Lugar
from app.plantillas import DIAS, ETIQUETAS_CATEGORIA, MESES, templates

router = APIRouter()

DURACION_SUPUESTA = timedelta(hours=3)  # con hora y sin fin: lo damos por terminado a las 3 h
DIAS_ADELANTE = 45                      # hasta dónde mira "más adelante"
FINDE = (4, 5, 6)                       # viernes, sábado y domingo


def vigente_hasta(inicio: datetime, fin: datetime | None, hora_confirmada: bool, tz: ZoneInfo) -> datetime:
    """Hasta cuándo tiene sentido mostrar el evento."""
    if fin is not None:
        return fin
    if hora_confirmada:
        return inicio + DURACION_SUPUESTA
    # Sin hora: vale todo ese día
    return datetime.combine(inicio.astimezone(tz).date() + timedelta(days=1), time(0, 0), tzinfo=tz)


def dias_del_finde(hoy: date) -> list[date]:
    """El próximo bloque viernes-domingo que tenga al menos un día después de hoy.

    Hoy nunca entra: lo de hoy ya está en "Hoy". Un domingo, el finde es el de la semana que viene.
    """
    if hoy.weekday() in (4, 5):  # viernes o sábado: lo que queda de este finde
        return [hoy + timedelta(days=d) for d in range(1, 7 - hoy.weekday())]
    viernes = hoy + timedelta(days=(4 - hoy.weekday()) % 7 or 7)
    return [viernes + timedelta(days=d) for d in range(3)]


@dataclass
class Grupo:
    clave: str
    titulo: str
    bajada: str
    eventos: list = field(default_factory=list)


def _rango(dias: list[date]) -> str:
    a, b = dias[0], dias[-1]
    if a == b:
        return f"{DIAS[a.weekday()]} {a.day}"
    if a.month == b.month:
        return f"{DIAS[a.weekday()][:3]} {a.day} al {DIAS[b.weekday()][:3]} {b.day} de {MESES[b.month - 1]}"
    return f"{DIAS[a.weekday()][:3]} {a.day}/{a.month} al {DIAS[b.weekday()][:3]} {b.day}/{b.month}"


def agrupar(eventos: list, ahora: datetime, tz: ZoneInfo) -> list[Grupo]:
    hoy = ahora.astimezone(tz).date()
    finde = dias_del_finde(hoy)
    semana_fin = hoy + timedelta(days=7)
    grupos = {
        "hoy": Grupo("hoy", "Hoy", f"{DIAS[hoy.weekday()]} {hoy.day} de {MESES[hoy.month - 1]}"),
        "finde": Grupo("finde", "El finde", _rango(finde)),
        "semana": Grupo("semana", "Próximos 7 días", f"hasta el {DIAS[semana_fin.weekday()]} {semana_fin.day}"),
        "despues": Grupo("despues", "Más adelante", f"las próximas {DIAS_ADELANTE // 7} semanas"),
    }
    for ev in sorted(eventos, key=lambda e: e.inicio):
        if vigente_hasta(ev.inicio, ev.fin, ev.hora_confirmada, tz) <= ahora:
            continue
        dia = ev.inicio.astimezone(tz).date()
        if dia <= hoy:
            clave = "hoy"
        elif dia in finde:
            clave = "finde"
        elif dia <= semana_fin:
            clave = "semana"
        elif dia <= hoy + timedelta(days=DIAS_ADELANTE):
            clave = "despues"
        else:
            continue
        grupos[clave].eventos.append(ev)
    # El finde va antes que la semana solo si viene primero en el calendario
    orden = ["hoy", "finde", "semana", "despues"]
    if grupos["semana"].eventos and grupos["finde"].eventos and \
            min(e.inicio for e in grupos["semana"].eventos) < min(e.inicio for e in grupos["finde"].eventos):
        orden = ["hoy", "semana", "finde", "despues"]
    return [grupos[c] for c in orden]


# --- Consultas --------------------------------------------------------------------------------
def eventos_publicados(db: Session, ciudad: Ciudad, ahora: datetime, categoria: str = "", gratis: bool = False) -> list[Evento]:
    q = select(Evento).where(
        Evento.ciudad_id == ciudad.id,
        Evento.estado == "publicado",
        func.coalesce(Evento.fin, Evento.inicio) >= ahora - timedelta(days=1),
        Evento.inicio <= ahora + timedelta(days=DIAS_ADELANTE + 1),
    )
    if categoria:
        q = q.where(Evento.categoria == categoria)
    if gratis:
        q = q.where(Evento.gratis.is_(True))
    return list(db.scalars(q.order_by(Evento.inicio)))


def datos_portada(db: Session, ciudad: Ciudad, categoria: str, gratis: bool, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    tz = ZoneInfo(ciudad.timezone)
    categoria = categoria if categoria in ETIQUETAS_CATEGORIA else ""
    grupos = agrupar(eventos_publicados(db, ciudad, ahora, categoria, gratis), ahora, tz)
    # Para los chips: qué categorías tienen algo (sin el filtro de categoría)
    todas = agrupar(eventos_publicados(db, ciudad, ahora, "", gratis), ahora, tz)
    hay = {e.categoria for g in todas for e in g.eventos}
    return {
        "grupos": grupos, "total": sum(len(g.eventos) for g in grupos), "categoria": categoria, "gratis": gratis,
        "categorias_con_eventos": [c for c in ETIQUETAS_CATEGORIA if c in hay],
    }


# --- Detalle y calendario ------------------------------------------------------------------------
def _evento_publicado(db: Session, ciudad: Ciudad, slug_evento: str) -> Evento:
    ev = db.scalar(select(Evento).where(
        Evento.ciudad_id == ciudad.id, Evento.slug == slug_evento, Evento.estado == "publicado"))
    if ev is None:
        raise HTTPException(404, "No encontramos ese evento. Puede que lo hayan sacado de la agenda.")
    return ev


def _obtener_ciudad(db: Session, slug: str) -> Ciudad:
    from app.main import obtener_ciudad  # evita el import circular
    return obtener_ciudad(db, slug)


@router.get("/{slug}/evento/{slug_evento}", response_class=HTMLResponse)
def detalle(request: Request, slug: str, slug_evento: str, db: Session = Depends(get_db)):
    ciudad = _obtener_ciudad(db, slug)
    ev = _evento_publicado(db, ciudad, slug_evento)
    tz = ZoneInfo(ciudad.timezone)
    lugar = db.get(Lugar, ev.lugar_id) if ev.lugar_id else None
    punto = to_shape(lugar.geom) if lugar else None
    fuente = db.get(Fuente, ev.fuente_id) if ev.fuente_id else None
    paso = vigente_hasta(ev.inicio, ev.fin, ev.hora_confirmada, tz) <= datetime.now(timezone.utc)
    return templates.TemplateResponse(request, "evento.html", {
        "ciudad": ciudad, "ev": ev, "lugar": lugar, "punto": punto, "fuente": fuente, "paso": paso,
        "inicio_local": ev.inicio.astimezone(tz), "fin_local": ev.fin.astimezone(tz) if ev.fin else None,
        "url_sitio": get_settings().sitio_url.rstrip("/"),
    })


def _ics_texto(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _ics_doblar(linea: str) -> str:
    """RFC 5545: líneas de hasta 75 octetos, las siguientes arrancan con un espacio."""
    salida, actual = [], b""
    for c in linea:
        b = c.encode()
        if len(actual) + len(b) > (75 if not salida else 74):
            salida.append(actual.decode())
            actual = b""
        actual += b
    salida.append(actual.decode())
    return "\r\n ".join(salida)


def armar_ics(ev: Evento, ciudad: Ciudad, url: str, ahora: datetime) -> str:
    tz = ZoneInfo(ciudad.timezone)
    utc = "%Y%m%dT%H%M%SZ"
    if ev.hora_confirmada:
        fin = ev.fin or ev.inicio + DURACION_SUPUESTA
        cuando = [f"DTSTART:{ev.inicio.astimezone(timezone.utc):{utc}}", f"DTEND:{fin.astimezone(timezone.utc):{utc}}"]
    else:
        dia = ev.inicio.astimezone(tz).date()
        hasta = (ev.fin.astimezone(tz).date() if ev.fin else dia) + timedelta(days=1)
        cuando = [f"DTSTART;VALUE=DATE:{dia:%Y%m%d}", f"DTEND;VALUE=DATE:{hasta:%Y%m%d}"]
    descripcion = (ev.descripcion or "") + ("" if ev.hora_confirmada else "\n\nHorario a confirmar.") + f"\n\n{url}"
    lineas = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//quehacer//agenda//ES", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:evento-{ev.id}@{ciudad.slug}.quehacer",
        f"DTSTAMP:{ahora.astimezone(timezone.utc):{utc}}",
        *cuando,
        f"SUMMARY:{_ics_texto(ev.titulo)}",
        f"DESCRIPTION:{_ics_texto(descripcion.strip())}",
        f"URL:{url}",
    ]
    if ev.lugar_texto:
        lineas.append(f"LOCATION:{_ics_texto(ev.lugar_texto + ', ' + ciudad.nombre)}")
    lineas += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_ics_doblar(l) for l in lineas) + "\r\n"


@router.get("/{slug}/evento/{slug_evento}/calendario.ics")
def calendario(request: Request, slug: str, slug_evento: str, db: Session = Depends(get_db)):
    ciudad = _obtener_ciudad(db, slug)
    ev = _evento_publicado(db, ciudad, slug_evento)
    url = str(request.url_for("detalle", slug=slug, slug_evento=slug_evento))
    return Response(
        armar_ics(ev, ciudad, url, datetime.now(timezone.utc)), media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{ev.slug}.ics"'},
    )
