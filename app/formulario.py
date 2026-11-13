"""Formulario público "Cargá tu evento".

Cualquiera puede mandar un evento sin registrarse. Entra como **pendiente** y no se
publica hasta que alguien lo aprueba en /admin. Contra el spam: campo trampa, token
con la hora en que se mostró el formulario y un tope de envíos por IP y por hora.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import seguridad
from app.config import get_settings
from app.db import get_db
from app.ingesta import dedup
from app.ingesta.pipeline import buscar_lugar, slug_unico
from app.models import CATEGORIAS, Ciudad, Evento, Fuente
from app.plantillas import templates

log = logging.getLogger(__name__)
router = APIRouter()

CAMPO_TRAMPA = "sitio_web"  # oculto con CSS: un humano no lo ve ni lo completa
DIAS_MAX_ADELANTE = 365


@dataclass
class DatosEvento:
    titulo: str
    inicio: datetime
    fin: datetime | None
    hora_confirmada: bool
    lugar_texto: str
    categoria: str
    descripcion: str | None
    precio_texto: str | None
    gratis: bool | None
    url_origen: str | None
    contacto: str | None


def _texto(form, campo: str, largo: int) -> str:
    """Recorta, colapsa espacios y saca caracteres de control."""
    valor = str(form.get(campo) or "")
    valor = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", valor)
    valor = re.sub(r"[ \t]+", " ", valor).strip()
    return valor[: largo + 1]  # uno de más para poder avisar que se pasó


def _hora(valor: str) -> time | None:
    if not valor:
        return None
    try:
        return time.fromisoformat(valor)
    except ValueError:
        raise ValueError("La hora tiene que ser HH:MM.") from None


def validar_evento(form, tz: ZoneInfo, hoy: date, permitir_pasado: bool = False) -> tuple[DatosEvento | None, dict[str, str]]:
    """Devuelve (datos, errores). Se usa en el formulario público y en la edición del admin."""
    err: dict[str, str] = {}

    titulo = _texto(form, "titulo", 200)
    if len(titulo) < 4:
        err["titulo"] = "Poné un nombre para el evento (al menos 4 letras)."
    elif len(titulo) > 200:
        err["titulo"] = "El nombre es muy largo (hasta 200 caracteres)."

    fecha = None
    try:
        fecha = date.fromisoformat(_texto(form, "fecha", 10))
    except ValueError:
        err["fecha"] = "Elegí la fecha del evento."
    if fecha and not permitir_pasado and fecha < hoy:
        err["fecha"] = "La fecha ya pasó."
    elif fecha and fecha > hoy + timedelta(days=DIAS_MAX_ADELANTE):
        err["fecha"] = "Por ahora cargamos eventos de hasta un año para adelante."

    hora = hora_fin = None
    try:
        hora = _hora(_texto(form, "hora", 5))
    except ValueError as e:
        err["hora"] = str(e)
    try:
        hora_fin = _hora(_texto(form, "hora_fin", 5))
    except ValueError as e:
        err["hora_fin"] = str(e)
    if hora_fin and not hora and "hora" not in err:
        err["hora"] = "Si ponés hora de fin, poné también la de inicio."

    lugar = _texto(form, "lugar", 200)
    if len(lugar) < 2:
        err["lugar"] = "¿Dónde es? Un lugar o una dirección."
    elif len(lugar) > 200:
        err["lugar"] = "El lugar es muy largo (hasta 200 caracteres)."

    categoria = _texto(form, "categoria", 32) or "otro"
    if categoria not in CATEGORIAS:
        err["categoria"] = "Elegí una categoría de la lista."

    descripcion = _texto(form, "descripcion", 2000)
    if len(descripcion) > 2000:
        err["descripcion"] = "La descripción es muy larga (hasta 2000 caracteres)."
    precio = _texto(form, "precio", 200)
    if len(precio) > 200:
        err["precio"] = "Hasta 200 caracteres."
    contacto = _texto(form, "contacto", 200)
    if len(contacto) > 200:
        err["contacto"] = "Hasta 200 caracteres."

    enlace = _texto(form, "enlace", 500)
    if enlace:
        u = urlparse(enlace)
        if u.scheme not in ("http", "https") or not u.netloc or len(enlace) > 500:
            err["enlace"] = "Tiene que ser un link completo, que empiece con https://"

    gratis_valor = str(form.get("gratis") or "")
    gratis = {"si": True, "no": False}.get(gratis_valor)

    if err:
        return None, err

    inicio = datetime.combine(fecha, hora or time(0, 0), tzinfo=tz)
    fin = None
    if hora_fin:
        fin = datetime.combine(fecha, hora_fin, tzinfo=tz)
        if fin <= inicio:
            fin += timedelta(days=1)  # termina pasada la medianoche
    return DatosEvento(
        titulo=titulo, inicio=inicio, fin=fin, hora_confirmada=hora is not None, lugar_texto=lugar,
        categoria=categoria, descripcion=descripcion or None, precio_texto=precio or None,
        gratis=gratis, url_origen=enlace or None, contacto=contacto or None,
    ), {}


def fuente_formulario(db: Session, ciudad: Ciudad) -> Fuente:
    clave = f"formulario-{ciudad.slug}"
    f = db.scalar(select(Fuente).where(Fuente.clave == clave))
    if f is None:
        f = Fuente(ciudad_id=ciudad.id, clave=clave, nombre="Formulario web", tipo="formulario",
                   url=f"/{ciudad.slug}/cargar", activa=True)
        db.add(f)
        db.flush()
    return f


def envios_recientes(db: Session, ip_hash: str, ahora: datetime) -> int:
    return db.scalar(
        select(func.count()).select_from(Evento)
        .where(Evento.ip_hash == ip_hash, Evento.creado > ahora - timedelta(hours=1))
    )


def _obtener_ciudad(db: Session, slug: str) -> Ciudad:
    from app.main import obtener_ciudad  # evita el import circular
    return obtener_ciudad(db, slug)


def _render(request: Request, ciudad: Ciudad, valores: dict | None = None, errores: dict | None = None,
            aviso: str | None = None, status: int = 200) -> HTMLResponse:
    tz = ZoneInfo(ciudad.timezone)
    return templates.TemplateResponse(request, "cargar.html", {
        "ciudad": ciudad, "v": valores or {}, "errores": errores or {}, "aviso": aviso,
        "marca": seguridad.marca_formulario(), "trampa": CAMPO_TRAMPA,
        "hoy": datetime.now(tz).date().isoformat(),
        "max_fecha": (datetime.now(tz).date() + timedelta(days=DIAS_MAX_ADELANTE)).isoformat(),
    }, status_code=status)


@router.get("/{slug}/cargar", response_class=HTMLResponse)
def formulario(request: Request, slug: str, db: Session = Depends(get_db)):
    return _render(request, _obtener_ciudad(db, slug))


@router.get("/{slug}/cargar/gracias", response_class=HTMLResponse)
def gracias(request: Request, slug: str, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "cargar_gracias.html", {"ciudad": _obtener_ciudad(db, slug)})


@router.post("/{slug}/cargar", response_class=HTMLResponse)
async def enviar(request: Request, slug: str, db: Session = Depends(get_db)):
    ciudad = _obtener_ciudad(db, slug)
    form = await request.form()
    valores = {k: str(v) for k, v in form.items() if k not in ("marca", CAMPO_TRAMPA)}
    gracias_url = f"/{ciudad.slug}/cargar/gracias"

    # Bots: se les contesta como si hubiera salido bien, así no prueban otra cosa.
    if form.get(CAMPO_TRAMPA):
        log.info("Formulario: campo trampa completado, se descarta")
        return RedirectResponse(gracias_url, status_code=303)
    marca = seguridad.estado_marca(form.get("marca"))
    if marca in ("invalido", "rapido"):
        log.info("Formulario: marca %s, se descarta", marca)
        return RedirectResponse(gracias_url, status_code=303)
    if marca == "vencido":
        return _render(request, ciudad, valores, aviso="El formulario venció. Revisá los datos y mandalo de nuevo.", status=400)

    ahora = datetime.now(timezone.utc)
    ip_hash = seguridad.hash_ip(seguridad.ip_cliente(request))
    if envios_recientes(db, ip_hash, ahora) >= get_settings().max_envios_por_hora:
        return _render(request, ciudad, valores, status=429,
                       aviso="Recibimos varios eventos desde tu conexión en la última hora. Probá de nuevo más tarde.")

    tz = ZoneInfo(ciudad.timezone)
    datos, errores = validar_evento(form, tz, datetime.now(tz).date())
    if errores:
        return _render(request, ciudad, valores, errores, status=422)

    fuente = fuente_formulario(db, ciudad)
    ev = Evento(
        ciudad_id=ciudad.id,
        slug=slug_unico(db, datos.titulo, datos.inicio, tz),
        titulo=datos.titulo, descripcion=datos.descripcion, inicio=datos.inicio, fin=datos.fin,
        hora_confirmada=datos.hora_confirmada,
        lugar_id=buscar_lugar(db, ciudad.id, datos.lugar_texto), lugar_texto=datos.lugar_texto,
        precio_texto=datos.precio_texto, gratis=datos.gratis, categoria=datos.categoria,
        url_origen=datos.url_origen, urls_alternativas=[], fuente_id=fuente.id,
        estado="pendiente", confianza=0.0,  # no viene del LLM: lo decide quien modera
        hash_dedup=dedup.hash_dedup(dedup.DatosDedup(datos.titulo, datos.inicio, datos.lugar_texto), tz),
        contacto=datos.contacto, ip_hash=ip_hash,
    )
    db.add(ev)
    db.commit()
    log.info("Formulario: evento %s pendiente de revisión", ev.id)
    return RedirectResponse(gracias_url, status_code=303)
