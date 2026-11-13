"""Web pública (FastAPI + Jinja2 + HTMX)."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import get_db
from app.lugares.overpass import texto_horario
from app.models import Ciudad, CorridaFuente, Evento, Fuente, ItemCrudo, Lugar

BASE = Path(__file__).resolve().parent

app = FastAPI(title="quehacer", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

# Texto + ícono por tipo de lugar: nunca distinguimos solo por color.
TIPOS_ESPACIO_VERDE = {
    "plaza": {"etiqueta": "Plaza", "icono": "i-plaza"},
    "parque": {"etiqueta": "Parque", "icono": "i-parque"},
    "juegos": {"etiqueta": "Juegos infantiles", "icono": "i-juegos"},
    "jardin": {"etiqueta": "Jardín", "icono": "i-jardin"},
}

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def fecha_larga(tz: str = "America/Argentina/Rio_Gallegos") -> str:
    """Ej.: 'jueves 12 de noviembre'. Sin depender del locale del sistema."""
    hoy = datetime.now(ZoneInfo(tz))
    return f"{DIAS[hoy.weekday()]} {hoy.day} de {MESES[hoy.month - 1]}"


def formatear_distancia(metros: float | None) -> str:
    if metros is None:
        return ""
    if metros < 1000:
        return f"a {int(round(metros, -1))} m"
    return f"a {metros / 1000:.1f} km".replace(".", ",")


templates.env.filters["distancia"] = formatear_distancia
templates.env.globals["TIPOS"] = TIPOS_ESPACIO_VERDE
templates.env.globals["texto_horario"] = texto_horario
templates.env.globals["fecha_larga"] = fecha_larga
_nombre = get_settings().sitio_nombre.strip()
templates.env.globals["SITIO_NOMBRE"] = _nombre
# Para el logo: primera palabra en negativo, el resto al lado
templates.env.globals["LOGO"] = (_nombre.split(" ", 1) + [""])[:2]


def obtener_ciudad(db: Session, slug: str) -> Ciudad:
    ciudad = db.scalar(select(Ciudad).where(Ciudad.slug == slug))
    if ciudad is None:
        raise HTTPException(404, "No tenemos esa ciudad (todavía).")
    return ciudad


def hace(momento: datetime | None) -> str:
    """'hace 5 min', 'hace 3 h', 'hace 2 días'."""
    if momento is None:
        return "nunca"
    seg = (datetime.now(momento.tzinfo) - momento).total_seconds()
    if seg < 90:
        return "recién"
    if seg < 3600:
        return f"hace {int(seg // 60)} min"
    if seg < 86400:
        return f"hace {int(seg // 3600)} h"
    dias = int(seg // 86400)
    return f"hace {dias} día" + ("s" if dias != 1 else "")


def hora_local(momento: datetime | None, tz: str = "America/Argentina/Rio_Gallegos") -> str:
    if momento is None:
        return "—"
    m = momento.astimezone(ZoneInfo(tz))
    return f"{DIAS[m.weekday()][:3]} {m.day}/{m.month} {m:%H:%M}"


templates.env.filters["hace"] = hace
templates.env.filters["hora_local"] = hora_local

ESTADOS_FUENTE = {
    "ok": {"etiqueta": "Funciona", "icono": "i-ok"},
    "aviso": {"etiqueta": "Funciona, con avisos", "icono": "i-aviso"},
    "error": {"etiqueta": "Con error", "icono": "i-error"},
    "nunca": {"etiqueta": "Todavía no corrió", "icono": "i-vacio"},
    "pausada": {"etiqueta": "Pausada", "icono": "i-pausa"},
}
ESTADOS_EVENTO = {
    "publicado": {"etiqueta": "Publicado", "icono": "i-ok"},
    "pendiente": {"etiqueta": "Pendiente de revisión", "icono": "i-reloj"},
    "rechazado": {"etiqueta": "Rechazado", "icono": "i-error"},
}
templates.env.globals["ESTADOS_FUENTE"] = ESTADOS_FUENTE
templates.env.globals["ESTADOS_EVENTO"] = ESTADOS_EVENTO


def _coordenada(valor: str, limite: float) -> float | None:
    """Los campos ocultos llegan vacíos si no se pidió la ubicación."""
    try:
        v = float(valor)
    except ValueError:
        return None
    return v if -limite <= v <= limite else None


@app.get("/salud")
def salud(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@app.get("/estado/fuentes", response_class=HTMLResponse)
def estado_fuentes(request: Request, db: Session = Depends(get_db)):
    """Panel de salud de las fuentes (público por ahora; pasa a /admin en la fase 3)."""
    filas = []
    for f in db.scalars(select(Fuente).order_by(Fuente.activa.desc(), Fuente.nombre)):
        corridas = db.scalars(
            select(CorridaFuente).where(CorridaFuente.fuente_id == f.id)
            .order_by(CorridaFuente.inicio.desc()).limit(6)
        ).all()
        ultima = corridas[0] if corridas else None
        if not f.activa:
            estado = "pausada"
        elif ultima is None:
            estado = "nunca"
        elif ultima.ok is False:
            estado = "error"
        elif ultima.avisos:
            estado = "aviso"
        else:
            estado = "ok"
        conteos = dict(db.execute(
            select(ItemCrudo.paso_prefiltro, func.count()).where(ItemCrudo.fuente_id == f.id)
            .group_by(ItemCrudo.paso_prefiltro)
        ).all())
        eventos = db.scalar(select(func.count()).select_from(Evento).where(Evento.fuente_id == f.id))
        filas.append({
            "f": f, "estado": estado, "ultima": ultima, "corridas": corridas,
            "items": sum(conteos.values()), "prefiltro": conteos.get(True, 0), "eventos": eventos,
        })
    eventos = db.execute(
        select(Evento, Fuente.nombre).join(Fuente, Evento.fuente_id == Fuente.id, isouter=True)
        .order_by(Evento.creado.desc()).limit(40)
    ).all()
    por_estado = dict(db.execute(select(Evento.estado, func.count()).group_by(Evento.estado)).all())
    ciudad = db.scalar(select(Ciudad).order_by(Ciudad.id))
    return templates.TemplateResponse(request, "estado_fuentes.html", {
        "filas": filas, "eventos": eventos, "por_estado": por_estado, "ciudad": ciudad,
        "con_error": sum(1 for x in filas if x["estado"] == "error"),
    })


@app.get("/")
def inicio(db: Session = Depends(get_db)):
    ciudad = db.scalar(select(Ciudad).order_by(Ciudad.id))
    if ciudad is None:
        return HTMLResponse("Todavía no hay ciudades cargadas.", status_code=503)
    return RedirectResponse(f"/{ciudad.slug}", status_code=302)


@app.get("/{slug}", response_class=HTMLResponse)
def portada(request: Request, slug: str, db: Session = Depends(get_db)):
    ciudad = obtener_ciudad(db, slug)
    cant_lugares = db.scalar(
        select(func.count()).select_from(Lugar).where(
            Lugar.ciudad_id == ciudad.id, Lugar.tipo.in_(TIPOS_ESPACIO_VERDE)
        )
    )
    return templates.TemplateResponse(
        request, "portada.html", {"ciudad": ciudad, "cant_lugares": cant_lugares}
    )


@app.get("/{slug}/plazas", response_class=HTMLResponse)
def plazas(
    request: Request,
    slug: str,
    tipo: str = Query("", description="Filtrar por tipo"),
    q: str = Query("", max_length=100),
    lat: str = Query("", max_length=20),
    lon: str = Query("", max_length=20),
    db: Session = Depends(get_db),
):
    ciudad = obtener_ciudad(db, slug)
    lat_f, lon_f = _coordenada(lat, 90), _coordenada(lon, 180)
    tipos = [tipo] if tipo in TIPOS_ESPACIO_VERDE else list(TIPOS_ESPACIO_VERDE)

    distancia = None
    if lat_f is not None and lon_f is not None:
        punto = func.ST_SetSRID(func.ST_MakePoint(lon_f, lat_f), 4326)
        distancia = func.ST_DistanceSphere(Lugar.geom, punto)

    consulta = select(Lugar, distancia if distancia is not None else text("NULL")).where(
        Lugar.ciudad_id == ciudad.id, Lugar.tipo.in_(tipos)
    )
    if q.strip():
        consulta = consulta.where(Lugar.nombre.ilike(f"%{q.strip()}%"))
    consulta = consulta.order_by(distancia if distancia is not None else Lugar.nombre.asc().nulls_last())

    filas = []
    for lugar, metros in db.execute(consulta):
        punto = to_shape(lugar.geom)
        filas.append({"l": lugar, "lat": punto.y, "lon": punto.x, "metros": metros})

    contexto = {
        "ciudad": ciudad,
        "con_nombre": [f for f in filas if f["l"].nombre],
        "sin_nombre": [f for f in filas if not f["l"].nombre],
        "tipo": tipo if tipo in TIPOS_ESPACIO_VERDE else "",
        "q": q,
        "ordenado_por_cercania": distancia is not None,
    }
    centro = to_shape(ciudad.bbox).centroid
    contexto["centro"] = {"lat": centro.y, "lon": centro.x}

    # Con HTMX solo devolvemos la lista; sin JS se renderiza la página entera.
    es_parcial = request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request")
    plantilla = "parciales/lista_lugares.html" if es_parcial else "plazas.html"
    return templates.TemplateResponse(request, plantilla, contexto)


@app.exception_handler(StarletteHTTPException)
def error_http(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/static") or request.url.path == "/salud":
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    mensaje = exc.detail if exc.status_code == 404 and exc.detail != "Not Found" else (
        "No encontramos esa página." if exc.status_code == 404 else "Algo salió mal."
    )
    return templates.TemplateResponse(
        request, "error.html", {"mensaje": mensaje, "codigo": exc.status_code}, status_code=exc.status_code
    )
