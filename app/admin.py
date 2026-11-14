"""Panel de administración: moderar eventos y ver la salud de las fuentes.

Un solo usuario (ADMIN_USUARIO / ADMIN_CLAVE en .env). La sesión es una cookie firmada
con HMAC; cada formulario que cambia algo lleva un token CSRF atado a esa sesión.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import seguridad
from app.db import get_db
from app.formulario import validar_evento
from app.ingesta import dedup, heuristica
from app.ingesta.lectores import _resumen
from app.ingesta.pipeline import buscar_lugar, slug_unico
from app.models import ESTADOS_EVENTO, Ciudad, CorridaFuente, Evento, Fuente, ItemCrudo
from app.plantillas import templates

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")

ACCIONES = {"publicar": "publicado", "rechazar": "rechazado", "pendiente": "pendiente"}
POR_PAGINA = 100


class NecesitaLogin(Exception):
    def __init__(self, siguiente: str):
        self.siguiente = siguiente


def _siguiente_seguro(valor: str | None) -> str:
    """Solo rutas internas del panel (evita redirecciones abiertas)."""
    if valor and valor.startswith("/admin") and not urlparse(valor).netloc and "\\" not in valor:
        return valor
    return "/admin/eventos"


def requiere_admin(request: Request) -> str:
    """Devuelve la cookie de sesión (sirve para el CSRF)."""
    if not seguridad.admin_habilitado():
        raise HTTPException(503, "El panel está deshabilitado: falta ADMIN_CLAVE en el .env.")
    cookie = request.cookies.get(seguridad.COOKIE_SESION)
    if not seguridad.sesion_valida(cookie):
        destino = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise NecesitaLogin(destino if request.method == "GET" else "/admin/eventos")
    return cookie


async def requiere_admin_csrf(request: Request, cookie: str = Depends(requiere_admin)) -> str:
    form = await request.form()
    if not seguridad.csrf_valido(cookie, form.get("csrf")):
        raise HTTPException(403, "El formulario venció. Volvé a cargar la página.")
    return cookie


def _ctx(request: Request, cookie: str, **extra) -> dict:
    return {"request": request, "csrf": seguridad.token_csrf(cookie), "ciudad": None, **extra}


# --- Entrar / salir ---------------------------------------------------------------------------
@router.get("/entrar", response_class=HTMLResponse)
def form_entrar(request: Request, siguiente: str = ""):
    if not seguridad.admin_habilitado():
        raise HTTPException(503, "El panel está deshabilitado: falta ADMIN_CLAVE en el .env.")
    if seguridad.sesion_valida(request.cookies.get(seguridad.COOKIE_SESION)):
        return RedirectResponse(_siguiente_seguro(siguiente), status_code=303)
    return templates.TemplateResponse(request, "admin/entrar.html", {"siguiente": _siguiente_seguro(siguiente), "ciudad": None})


@router.post("/entrar", response_class=HTMLResponse)
async def entrar(request: Request):
    if not seguridad.admin_habilitado():
        raise HTTPException(503, "El panel está deshabilitado: falta ADMIN_CLAVE en el .env.")
    form = await request.form()
    siguiente = _siguiente_seguro(form.get("siguiente"))
    ip = seguridad.ip_cliente(request)

    def error(mensaje: str, status: int):
        return templates.TemplateResponse(request, "admin/entrar.html", {
            "siguiente": siguiente, "error": mensaje, "usuario": str(form.get("usuario") or "")[:80], "ciudad": None,
        }, status_code=status)

    if seguridad.limite_login.bloqueado(ip):
        return error("Demasiados intentos fallidos. Esperá 15 minutos.", 429)
    if not seguridad.credenciales_validas(str(form.get("usuario") or ""), str(form.get("clave") or "")):
        seguridad.limite_login.registrar(ip)
        log.warning("Admin: login fallido")
        return error("Usuario o clave incorrectos.", 401)

    seguridad.limite_login.resetear(ip)
    resp = RedirectResponse(siguiente, status_code=303)
    resp.set_cookie(
        seguridad.COOKIE_SESION, seguridad.crear_sesion(), max_age=seguridad.DURACION_SESION,
        path="/admin", httponly=True, samesite="lax", secure=request.url.scheme == "https",
    )
    return resp


@router.post("/salir")
def salir(_: str = Depends(requiere_admin_csrf)):
    resp = RedirectResponse("/admin/entrar", status_code=303)
    resp.delete_cookie(seguridad.COOKIE_SESION, path="/admin")
    return resp


@router.get("")
@router.get("/")
def inicio(_: str = Depends(requiere_admin)):
    return RedirectResponse("/admin/eventos", status_code=303)


# --- Eventos ----------------------------------------------------------------------------------
def _posibles_duplicados(db: Session, eventos: list[Evento]) -> dict[int, list[Evento]]:
    """Para cada evento, otros que parecen el mismo (±1 día, título/lugar parecidos)."""
    if not eventos:
        return {}
    desde = min(e.inicio for e in eventos) - timedelta(days=1)
    hasta = max(e.inicio for e in eventos) + timedelta(days=1)
    cercanos = db.scalars(select(Evento).where(Evento.inicio.between(desde, hasta))).all()
    salida: dict[int, list[Evento]] = {}
    for e in eventos:
        a = dedup.DatosDedup(e.titulo, e.inicio, e.lugar_texto)
        iguales = [
            o for o in cercanos
            if o.id != e.id and o.ciudad_id == e.ciudad_id
            and dedup.es_mismo_evento(a, dedup.DatosDedup(o.titulo, o.inicio, o.lugar_texto))
        ]
        if iguales:
            salida[e.id] = iguales
    return salida


def _datos_lista(db: Session, estado: str, cuando: str, ahora: datetime) -> dict:
    q = select(Evento, Fuente).join(Fuente, Evento.fuente_id == Fuente.id, isouter=True).where(Evento.estado == estado)
    # "Próximos" incluye los de hoy aunque ya hayan empezado (o sin hora confirmada)
    corte = ahora - timedelta(hours=12)
    if cuando == "proximos":
        q = q.where(func.coalesce(Evento.fin, Evento.inicio) >= corte).order_by(Evento.inicio.asc())
    elif cuando == "pasados":
        q = q.where(func.coalesce(Evento.fin, Evento.inicio) < corte).order_by(Evento.inicio.desc())
    else:
        q = q.order_by(Evento.inicio.desc())
    filas = db.execute(q.limit(POR_PAGINA + 1)).all()
    hay_mas = len(filas) > POR_PAGINA
    filas = filas[:POR_PAGINA]
    conteos = dict(db.execute(select(Evento.estado, func.count()).group_by(Evento.estado)).all())
    return {
        "filas": filas, "hay_mas": hay_mas, "conteos": conteos, "estado": estado, "cuando": cuando,
        "duplicados": _posibles_duplicados(db, [ev for ev, _ in filas]),
        "ciudades": {c.id: c for c in db.scalars(select(Ciudad))},
    }


@router.get("/eventos", response_class=HTMLResponse)
def eventos(
    request: Request,
    estado: str = Query("pendiente"),
    cuando: str = Query("proximos"),
    ok: str = Query(""),
    cookie: str = Depends(requiere_admin),
    db: Session = Depends(get_db),
):
    estado = estado if estado in ESTADOS_EVENTO else "pendiente"
    cuando = cuando if cuando in ("proximos", "pasados", "todos") else "proximos"
    datos = _datos_lista(db, estado, cuando, datetime.now(timezone.utc))
    volver = f"/admin/eventos?estado={estado}&cuando={cuando}"
    return templates.TemplateResponse(request, "admin/eventos.html", _ctx(request, cookie, ok=ok[:100], volver=volver, **datos))


@router.post("/eventos/{evento_id}/estado", response_class=HTMLResponse)
async def cambiar_estado(
    request: Request, evento_id: int, cookie: str = Depends(requiere_admin_csrf), db: Session = Depends(get_db),
):
    form = await request.form()
    nuevo = ACCIONES.get(str(form.get("accion")))
    ev = db.get(Evento, evento_id)
    if ev is None:
        raise HTTPException(404, "Ese evento no existe.")
    if nuevo is None:
        raise HTTPException(400, "Acción desconocida.")
    ev.estado, ev.moderado = nuevo, datetime.now(timezone.utc)
    db.commit()
    log.info("Admin: evento %s -> %s", ev.id, nuevo)

    if request.headers.get("HX-Request"):
        fuente = db.get(Fuente, ev.fuente_id) if ev.fuente_id else None
        return templates.TemplateResponse(request, "admin/parciales/fila_evento.html", _ctx(
            request, cookie, ev=ev, fuente=fuente, recien=nuevo, duplicados={},
            ciudades={c.id: c for c in db.scalars(select(Ciudad))},
        ))
    volver = _siguiente_seguro(str(form.get("volver") or ""))
    return RedirectResponse(volver, status_code=303)


@router.get("/eventos/{evento_id}", response_class=HTMLResponse)
def editar(request: Request, evento_id: int, cookie: str = Depends(requiere_admin), db: Session = Depends(get_db)):
    ev = db.get(Evento, evento_id)
    if ev is None:
        raise HTTPException(404, "Ese evento no existe.")
    return _render_edicion(request, cookie, db, ev, _valores_de(ev, db))


def _valores_de(ev: Evento, db: Session) -> dict:
    tz = ZoneInfo(db.get(Ciudad, ev.ciudad_id).timezone)
    ini = ev.inicio.astimezone(tz)
    return {
        "titulo": ev.titulo, "fecha": ini.date().isoformat(),
        "hora": ini.strftime("%H:%M") if ev.hora_confirmada else "",
        "hora_fin": ev.fin.astimezone(tz).strftime("%H:%M") if ev.fin else "",
        "lugar": ev.lugar_texto or "", "categoria": ev.categoria, "descripcion": ev.descripcion or "",
        "precio": ev.precio_texto or "", "gratis": {True: "si", False: "no"}.get(ev.gratis, ""),
        "enlace": ev.url_origen or "", "contacto": ev.contacto or "", "estado": ev.estado,
    }


def _render_edicion(request, cookie, db, ev, valores, errores=None, status=200):
    fuente = db.get(Fuente, ev.fuente_id) if ev.fuente_id else None
    item = db.get(ItemCrudo, ev.item_crudo_id) if ev.item_crudo_id else None
    return templates.TemplateResponse(request, "admin/editar.html", _ctx(
        request, cookie, ev=ev, v=valores, errores=errores or {}, fuente=fuente, item=item,
        duplicados=_posibles_duplicados(db, [ev]).get(ev.id, []),
    ), status_code=status)


@router.post("/eventos/{evento_id}", response_class=HTMLResponse)
async def guardar(request: Request, evento_id: int, cookie: str = Depends(requiere_admin_csrf), db: Session = Depends(get_db)):
    ev = db.get(Evento, evento_id)
    if ev is None:
        raise HTTPException(404, "Ese evento no existe.")
    form = await request.form()
    valores = {k: str(v) for k, v in form.items() if k != "csrf"}
    ciudad = db.get(Ciudad, ev.ciudad_id)
    tz = ZoneInfo(ciudad.timezone)
    datos, errores = validar_evento(form, tz, datetime.now(tz).date(), permitir_pasado=True)
    estado = str(form.get("estado") or ev.estado)
    if estado not in ESTADOS_EVENTO:
        errores["estado"] = "Estado inválido."
    if errores:
        return _render_edicion(request, cookie, db, ev, valores, errores, status=422)

    if datos.lugar_texto != ev.lugar_texto:
        ev.lugar_id = buscar_lugar(db, ciudad.id, datos.lugar_texto)
    for campo in ("titulo", "inicio", "fin", "hora_confirmada", "lugar_texto", "categoria", "descripcion",
                  "precio_texto", "gratis", "url_origen", "contacto"):
        setattr(ev, campo, getattr(datos, campo))
    ev.hash_dedup = dedup.hash_dedup(dedup.DatosDedup(ev.titulo, ev.inicio, ev.lugar_texto), tz)
    ev.estado, ev.moderado = estado, datetime.now(timezone.utc)
    db.commit()
    log.info("Admin: evento %s editado (%s)", ev.id, estado)
    return RedirectResponse(f"/admin/eventos?estado={estado}&ok={quote('Guardado: ' + ev.titulo[:60])}", status_code=303)


# --- Notas para revisar (sin LLM) -------------------------------------------------------------
def _notas_pendientes(db: Session):
    """Notas que pasaron el prefiltro y nadie procesó todavía (ni el LLM ni una persona)."""
    return (
        select(ItemCrudo, Fuente).join(Fuente, ItemCrudo.fuente_id == Fuente.id)
        .where(ItemCrudo.paso_prefiltro.is_(True), ItemCrudo.procesado.is_(None),
               Fuente.tipo.in_(("rss", "html", "api")))
    )


def _sugerencia(item: ItemCrudo, ciudad: Ciudad, hoy) -> dict:
    tz = ZoneInfo(ciudad.timezone)
    pub = (item.fecha_publicacion or item.creado).astimezone(tz).date()
    s = heuristica.sugerir(item.titulo or "", item.texto or "", pub)
    futuras = [f for f in s.fechas if f.dia >= hoy]
    texto = heuristica.normalizar(f"{item.titulo or ''} {item.texto or ''}")
    return {
        "s": s, "futuras": futuras, "publicado": pub,
        "local": heuristica.normalizar(ciudad.nombre) in texto,
    }


@router.get("/notas", response_class=HTMLResponse)
def notas(request: Request, ver: str = Query("futuras"), ok: str = Query(""),
          cookie: str = Depends(requiere_admin), db: Session = Depends(get_db)):
    ver = ver if ver in ("futuras", "todas") else "futuras"
    ciudades = {c.id: c for c in db.scalars(select(Ciudad))}
    filas = []
    for item, fuente in db.execute(_notas_pendientes(db).order_by(ItemCrudo.fecha_publicacion.desc().nullslast())):
        ciudad = ciudades[fuente.ciudad_id]
        hoy = datetime.now(ZoneInfo(ciudad.timezone)).date()
        filas.append({"item": item, "fuente": fuente, **_sugerencia(item, ciudad, hoy)})
    con_fechas = [f for f in filas if f["futuras"]]
    mostradas = con_fechas if ver == "futuras" else filas
    # Primero las que nombran la ciudad, después por fecha del evento más cercano
    mostradas.sort(key=lambda f: (not f["local"], f["futuras"][0].dia if f["futuras"] else datetime.max.date()))
    return templates.TemplateResponse(request, "admin/notas.html", _ctx(
        request, cookie, filas=mostradas[:POR_PAGINA], total=len(filas), con_fechas=len(con_fechas),
        ver=ver, ok=ok[:100], hay_mas=len(mostradas) > POR_PAGINA,
    ))


def _nota(db: Session, item_id: int) -> tuple[ItemCrudo, Fuente, Ciudad]:
    item = db.get(ItemCrudo, item_id)
    fuente = db.get(Fuente, item.fuente_id) if item else None
    if item is None or fuente is None:
        raise HTTPException(404, "Esa nota no existe.")
    return item, fuente, db.get(Ciudad, fuente.ciudad_id)


@router.post("/notas/{item_id}/descartar", response_class=HTMLResponse)
def descartar_nota(request: Request, item_id: int, _: str = Depends(requiere_admin_csrf), db: Session = Depends(get_db)):
    item, _f, _c = _nota(db, item_id)
    if item.procesado is None:
        item.procesado, item.error = datetime.now(timezone.utc), "Descartada en el panel"
        db.commit()
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse("/admin/notas", status_code=303)


@router.post("/notas/descartar-sin-fecha")
def descartar_sin_fecha(_: str = Depends(requiere_admin_csrf), db: Session = Depends(get_db)):
    """Descarta de una vez las notas en las que no se encontró ninguna fecha que venga."""
    ciudades = {c.id: c for c in db.scalars(select(Ciudad))}
    ahora, n = datetime.now(timezone.utc), 0
    for item, fuente in db.execute(_notas_pendientes(db)).all():
        ciudad = ciudades[fuente.ciudad_id]
        if not _sugerencia(item, ciudad, datetime.now(ZoneInfo(ciudad.timezone)).date())["futuras"]:
            item.procesado, item.error = ahora, "Descartada en el panel (sin fechas próximas)"
            n += 1
    db.commit()
    log.info("Admin: %s notas sin fecha descartadas", n)
    return RedirectResponse(f"/admin/notas?ok={quote(f'Se descartaron {n} notas sin fechas próximas')}", status_code=303)


def _render_nota(request, cookie, item, fuente, ciudad, valores, sug, errores=None, status=200):
    return templates.TemplateResponse(request, "admin/nota.html", _ctx(
        request, cookie, item=item, fuente=fuente, v=valores, errores=errores or {}, sug=sug,
    ), status_code=status)


@router.get("/notas/{item_id}", response_class=HTMLResponse)
def nota(request: Request, item_id: int, f: int = Query(0), cookie: str = Depends(requiere_admin), db: Session = Depends(get_db)):
    item, fuente, ciudad = _nota(db, item_id)
    sug = _sugerencia(item, ciudad, datetime.now(ZoneInfo(ciudad.timezone)).date())
    fechas = sug["futuras"]
    elegida = fechas[f] if 0 <= f < len(fechas) else (fechas[0] if fechas else None)
    valores = {
        "titulo": (item.titulo or "")[:200],
        "fecha": elegida.dia.isoformat() if elegida else "",
        "hora": elegida.hora.strftime("%H:%M") if elegida and elegida.hora else "",
        "lugar": sug["s"].lugar or "", "categoria": sug["s"].categoria,
        "descripcion": _resumen(item.texto) or "", "enlace": item.url, "estado": "publicado",
    }
    return _render_nota(request, cookie, item, fuente, ciudad, valores, sug)


@router.post("/notas/{item_id}", response_class=HTMLResponse)
async def crear_desde_nota(request: Request, item_id: int, cookie: str = Depends(requiere_admin_csrf),
                           db: Session = Depends(get_db)):
    item, fuente, ciudad = _nota(db, item_id)
    form = await request.form()
    valores = {k: str(v) for k, v in form.items() if k != "csrf"}
    tz = ZoneInfo(ciudad.timezone)
    datos, errores = validar_evento(form, tz, datetime.now(tz).date(), permitir_pasado=True)
    estado = str(form.get("estado") or "publicado")
    if estado not in ("publicado", "pendiente"):
        errores["estado"] = "Estado inválido."
    if errores:
        sug = _sugerencia(item, ciudad, datetime.now(tz).date())
        return _render_nota(request, cookie, item, fuente, ciudad, valores, sug, errores, status=422)

    ahora = datetime.now(timezone.utc)
    ev = Evento(
        ciudad_id=ciudad.id, slug=slug_unico(db, datos.titulo, datos.inicio, tz),
        titulo=datos.titulo, descripcion=datos.descripcion, inicio=datos.inicio, fin=datos.fin,
        hora_confirmada=datos.hora_confirmada, lugar_id=buscar_lugar(db, ciudad.id, datos.lugar_texto),
        lugar_texto=datos.lugar_texto, precio_texto=datos.precio_texto, gratis=datos.gratis,
        categoria=datos.categoria, url_origen=datos.url_origen or item.url, urls_alternativas=[],
        fuente_id=fuente.id, item_crudo_id=item.id, estado=estado, confianza=0.0, moderado=ahora,
        hash_dedup=dedup.hash_dedup(dedup.DatosDedup(datos.titulo, datos.inicio, datos.lugar_texto), tz),
        contacto=datos.contacto,
    )
    db.add(ev)
    db.flush()
    otro = bool(form.get("otro"))
    if not otro:
        item.procesado = ahora
    item.extraccion = {"manual": True, "eventos": [*((item.extraccion or {}).get("eventos") or []), ev.id]}
    item.error = None
    db.commit()
    log.info("Admin: evento %s creado a mano desde la nota %s", ev.id, item.id)
    if otro:
        return RedirectResponse(f"/admin/notas/{item.id}", status_code=303)
    return RedirectResponse(f"/admin/notas?ok={quote('Creado: ' + ev.titulo[:60])}", status_code=303)


# --- Fuentes ----------------------------------------------------------------------------------
@router.get("/fuentes", response_class=HTMLResponse)
def fuentes(request: Request, cookie: str = Depends(requiere_admin), db: Session = Depends(get_db)):
    filas = []
    for f in db.scalars(select(Fuente).order_by(Fuente.activa.desc(), Fuente.nombre)):
        corridas = db.scalars(
            select(CorridaFuente).where(CorridaFuente.fuente_id == f.id)
            .order_by(CorridaFuente.inicio.desc()).limit(6)
        ).all()
        ultima = corridas[0] if corridas else None
        if not f.activa:
            estado = "pausada"
        elif f.tipo == "formulario":
            estado = "ok"  # no corre: recibe envíos
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
        cant_eventos = db.scalar(select(func.count()).select_from(Evento).where(Evento.fuente_id == f.id))
        filas.append({
            "f": f, "estado": estado, "ultima": ultima, "corridas": corridas,
            "items": sum(conteos.values()), "prefiltro": conteos.get(True, 0), "eventos": cant_eventos,
        })
    ultimos = db.execute(
        select(Evento, Fuente.nombre).join(Fuente, Evento.fuente_id == Fuente.id, isouter=True)
        .order_by(Evento.creado.desc()).limit(40)
    ).all()
    por_estado = dict(db.execute(select(Evento.estado, func.count()).group_by(Evento.estado)).all())
    return templates.TemplateResponse(request, "admin/fuentes.html", _ctx(
        request, cookie, filas=filas, eventos=ultimos, por_estado=por_estado,
        con_error=sum(1 for x in filas if x["estado"] == "error"),
    ))
