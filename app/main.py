"""Web pública (FastAPI + Jinja2 + HTMX)."""
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import admin, agenda, formulario
from app.db import get_db
from app.models import Ciudad, Lugar
from app.plantillas import BASE, TIPOS_ESPACIO_VERDE, templates

app = FastAPI(title="quehacer", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
# Antes que /{slug}: si no, "/admin" se tomaría como una ciudad.
app.include_router(admin.router)
app.include_router(formulario.router)
app.include_router(agenda.router)


def obtener_ciudad(db: Session, slug: str) -> Ciudad:
    ciudad = db.scalar(select(Ciudad).where(Ciudad.slug == slug))
    if ciudad is None:
        raise HTTPException(404, "No tenemos esa ciudad (todavía).")
    return ciudad


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


@app.get("/estado/fuentes")
def estado_fuentes():
    """Se mudó al panel (pide login)."""
    return RedirectResponse("/admin/fuentes", status_code=301)


@app.get("/")
def inicio(db: Session = Depends(get_db)):
    ciudad = db.scalar(select(Ciudad).order_by(Ciudad.id))
    if ciudad is None:
        return HTMLResponse("Todavía no hay ciudades cargadas.", status_code=503)
    return RedirectResponse(f"/{ciudad.slug}", status_code=302)


@app.get("/{slug}", response_class=HTMLResponse)
def portada(
    request: Request,
    slug: str,
    cat: str = Query("", max_length=32),
    gratis: str = Query("", max_length=2),
    db: Session = Depends(get_db),
):
    ciudad = obtener_ciudad(db, slug)
    cant_lugares = db.scalar(
        select(func.count()).select_from(Lugar).where(
            Lugar.ciudad_id == ciudad.id, Lugar.tipo.in_(TIPOS_ESPACIO_VERDE)
        )
    )
    contexto = {"ciudad": ciudad, "cant_lugares": cant_lugares,
                **agenda.datos_portada(db, ciudad, cat, gratis == "1")}
    return templates.TemplateResponse(request, "portada.html", contexto)


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


@app.exception_handler(admin.NecesitaLogin)
def necesita_login(request: Request, exc: admin.NecesitaLogin):
    from urllib.parse import quote
    return RedirectResponse(f"/admin/entrar?siguiente={quote(exc.siguiente, safe='')}", status_code=303)


@app.exception_handler(StarletteHTTPException)
def error_http(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/static") or request.url.path == "/salud":
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 404:
        mensaje = exc.detail if exc.detail != "Not Found" else "No encontramos esa página."
    elif exc.status_code in (400, 403, 503) and isinstance(exc.detail, str):
        mensaje = exc.detail  # mensajes nuestros, pensados para mostrar
    else:
        mensaje = "Algo salió mal."
    return templates.TemplateResponse(
        request, "error.html", {"mensaje": mensaje, "codigo": exc.status_code}, status_code=exc.status_code
    )
