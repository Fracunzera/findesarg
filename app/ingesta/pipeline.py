"""Canalización de ingesta: fuente -> item_crudo -> prefiltro -> LLM -> evento (con dedup).

    índice (RSS / sitemap / listado / API)
        └─ candidatos nuevos (no vistos, no viejos, hasta N por corrida)
            └─ bajar nota (caché + robots) y sacar texto limpio con trafilatura
                └─ item_crudo  (se guarda SIEMPRE: auditoría y no reprocesar)
                    └─ prefiltro barato ──✗──> queda descartado
                        └─ LLM + Pydantic
                            └─ por cada evento: filtro de ciudad, dedup, publicado/pendiente
"""
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import trafilatura
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingesta import dedup, lectores
from app.ingesta.descarga import BloqueadoPorRobots, Descargador
from app.ingesta.extractor import ErrorExtraccion, EventoExtraido, Extractor, ExtraccionRechazada
from app.ingesta.fuentes import ConfigFuente
from app.ingesta.prefiltro import pasa_prefiltro
from app.models import Ciudad, CorridaFuente, Evento, Fuente, ItemCrudo, Lugar

log = logging.getLogger(__name__)

UMBRAL_PUBLICAR = 0.8
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MAX_INTENTOS_LLM = 3
EDAD_INDICE = 20 * 60  # los listados/RSS se vuelven a bajar si tienen más de 20 minutos


# --- Utilidades --------------------------------------------------------------------------
def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def slugify(texto: str, largo: int = 70) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", _sin_tildes(texto.lower())).strip("-")
    return t[:largo].rstrip("-") or "evento"


def misma_ciudad(a: str | None, b: str) -> bool:
    """'Río Gallegos', 'Rio Gallegos, Santa Cruz' y 'río gallegos (santa cruz)' son la misma."""
    if not a:
        return True  # el modelo no la dijo: asumimos la de la fuente
    na, nb = slugify(a), slugify(b)
    return na == nb or na.startswith(nb + "-") or fuzz.ratio(na, nb) >= 90


def decidir_estado(confianza: float, inicio: datetime, fin: datetime | None, hora_confirmada: bool,
                   tz: ZoneInfo, ahora: datetime | None = None) -> str:
    """Publicado si confianza >= 0.8 y el evento todavía no pasó; si no, pendiente.

    Sin hora confirmada, el evento 'sigue vigente' hasta el final de ese día.
    """
    ahora = ahora or datetime.now(timezone.utc)
    if fin is not None:
        limite = fin
    elif hora_confirmada:
        limite = inicio
    else:
        dia = inicio.astimezone(tz).date()
        limite = datetime.combine(dia, datetime.max.time(), tzinfo=tz)
    return "publicado" if confianza >= UMBRAL_PUBLICAR and limite > ahora else "pendiente"


def slug_unico(db: Session, titulo: str, inicio: datetime, tz: ZoneInfo) -> str:
    base = f"{slugify(titulo)}-{inicio.astimezone(tz).date().isoformat()}"
    slug, n = base, 2
    while db.scalar(select(Evento.id).where(Evento.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def buscar_lugar(db: Session, ciudad_id: int, nombre: str | None) -> int | None:
    if not nombre:
        return None
    objetivo = dedup.normalizar(nombre)
    if not objetivo:
        return None
    mejor, puntaje = None, 0.0
    for lugar_id, lugar_nombre in db.execute(
        select(Lugar.id, Lugar.nombre).where(Lugar.ciudad_id == ciudad_id, Lugar.nombre.is_not(None))
    ):
        p = fuzz.ratio(objetivo, dedup.normalizar(lugar_nombre))
        if p > puntaje:
            mejor, puntaje = lugar_id, p
    return mejor if puntaje >= 92 else None


# --- Guardado de eventos (con dedup) -----------------------------------------------------
@dataclass
class NuevoEvento:
    titulo: str
    inicio: datetime
    fin: datetime | None
    hora_confirmada: bool
    descripcion: str | None
    lugar_texto: str | None
    precio_texto: str | None
    gratis: bool | None
    categoria: str
    confianza: float
    url_origen: str


@dataclass
class ResultadoGuardado:
    evento: Evento
    nuevo: bool


def guardar_evento(db: Session, ciudad: Ciudad, fuente: Fuente, item: ItemCrudo | None,
                   ne: NuevoEvento, ahora: datetime | None = None) -> ResultadoGuardado:
    tz = ZoneInfo(ciudad.timezone)
    datos = dedup.DatosDedup(ne.titulo, ne.inicio, ne.lugar_texto)

    # Los rechazados también cuentan: si otra fuente trae el mismo evento, no vuelve a
    # aparecer como pendiente. Lo que llegó por el formulario y nadie revisó no se toca:
    # un envío anónimo no se publica solo porque un diario trae algo parecido.
    de_formulario = select(Fuente.id).where(Fuente.tipo == "formulario")
    candidatos = db.scalars(
        select(Evento).where(
            Evento.ciudad_id == ciudad.id,
            Evento.inicio.between(ne.inicio - timedelta(days=1), ne.inicio + timedelta(days=1)),
            ~((Evento.estado == "pendiente") & Evento.fuente_id.in_(de_formulario)),
        )
    ).all()
    for ev in candidatos:
        if dedup.es_mismo_evento(datos, dedup.DatosDedup(ev.titulo, ev.inicio, ev.lugar_texto)):
            _fusionar(ev, ne, tz, ahora)
            return ResultadoGuardado(ev, nuevo=False)

    ev = Evento(
        ciudad_id=ciudad.id,
        slug=slug_unico(db, ne.titulo, ne.inicio, tz),
        titulo=ne.titulo,
        descripcion=ne.descripcion,
        inicio=ne.inicio,
        fin=ne.fin,
        hora_confirmada=ne.hora_confirmada,
        lugar_id=buscar_lugar(db, ciudad.id, ne.lugar_texto),
        lugar_texto=ne.lugar_texto,
        precio_texto=ne.precio_texto,
        gratis=ne.gratis,
        categoria=ne.categoria,
        url_origen=ne.url_origen,
        urls_alternativas=[],
        fuente_id=fuente.id,
        item_crudo_id=item.id if item else None,
        confianza=ne.confianza,
        estado=decidir_estado(ne.confianza, ne.inicio, ne.fin, ne.hora_confirmada, tz, ahora),
        hash_dedup=dedup.hash_dedup(datos, tz),
    )
    db.add(ev)
    db.flush()
    return ResultadoGuardado(ev, nuevo=True)


def _fusionar(ev: Evento, ne: NuevoEvento, tz: ZoneInfo, ahora: datetime | None) -> None:
    """Se queda con el registro más completo y guarda las dos URLs de origen."""
    todas = set(ev.urls_alternativas or []) | {u for u in (ev.url_origen, ne.url_origen) if u}
    if ev.moderado is not None:
        # Ya lo revisó (y quizás corrigió) alguien: solo sumamos la URL.
        ev.urls_alternativas = sorted(todas - {ev.url_origen})
        return
    if dedup.completitud(ne) > dedup.completitud(ev):
        # El nuevo es mejor: pisa los datos principales (la URL vieja pasa a alternativa).
        for campo in ("titulo", "inicio", "fin", "hora_confirmada", "categoria", "url_origen"):
            setattr(ev, campo, getattr(ne, campo))
    ev.urls_alternativas = sorted(todas - {ev.url_origen})
    # Completar huecos con lo que traiga el otro
    for campo in ("descripcion", "fin", "lugar_texto", "precio_texto"):
        if not getattr(ev, campo) and getattr(ne, campo):
            setattr(ev, campo, getattr(ne, campo))
    if ev.gratis is None and ne.gratis is not None:
        ev.gratis = ne.gratis
    ev.confianza = max(ev.confianza or 0, ne.confianza)
    # Dos fuentes que coinciden suben la confianza. Lo moderado a mano no cambia de estado.
    if ev.estado == "pendiente" and ev.moderado is None:
        ev.estado = decidir_estado(ev.confianza, ev.inicio, ev.fin, ev.hora_confirmada, tz, ahora)


# --- Corrida de una fuente ---------------------------------------------------------------
@dataclass
class Contexto:
    db: Session
    descargador: Descargador
    extractor: Extractor | None  # None si no hay API key: los ítems quedan para después
    ahora: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _texto_de_nota(html: str, url: str) -> tuple[str | None, datetime | None]:
    texto = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_precision=True)
    fecha = None
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is not None and meta.date:
        try:
            fecha = datetime.fromisoformat(meta.date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return texto, fecha


def _candidatos(cfg: ConfigFuente, ctx: Contexto, ciudad: Ciudad, corrida: CorridaFuente):
    """Devuelve (candidatos de notas, eventos directos)."""
    d = ctx.descargador
    if cfg.tipo == "rss":
        return lectores.leer_rss(d.obtener(cfg.url, EDAD_INDICE)), []
    if cfg.tipo == "api" and cfg.formato == "muni_rg_noticias":
        return lectores.leer_muni_noticias(d.obtener(cfg.url, EDAD_INDICE), cfg.url_nota), []
    if cfg.tipo == "api" and cfg.formato == "muni_rg_calendario":
        return [], lectores.leer_muni_calendario(d.obtener(cfg.url, EDAD_INDICE), ciudad.timezone, cfg.sitio or cfg.url)
    if cfg.tipo == "html":
        salida: list[lectores.Candidato] = []
        for indice in cfg.indices:
            de_este: list[lectores.Candidato] = []
            for url in indice["urls"]:
                try:
                    contenido = d.obtener(url, EDAD_INDICE)
                except BloqueadoPorRobots as e:
                    corrida.avisos = [*corrida.avisos, str(e)]
                    continue
                if indice["tipo"] == "sitemap":
                    encontrados = lectores.leer_sitemap_noticias(contenido)
                    if not encontrados:
                        corrida.avisos = [*corrida.avisos, f"El sitemap {url} vino vacío"]
                    de_este += encontrados
                else:
                    de_este += lectores.leer_listado(contenido, url, indice["patron"])
            if indice.get("patron"):
                rx = re.compile(indice["patron"])
                de_este = [c for c in de_este if rx.search(c.url)]
            salida += de_este
        return salida, []
    raise ValueError(f"No sé leer la fuente {cfg.clave}")


def correr_fuente(ctx: Contexto, fuente: Fuente, cfg: ConfigFuente) -> CorridaFuente:
    s = get_settings()
    db = ctx.db
    ciudad = db.get(Ciudad, fuente.ciudad_id)
    corrida = CorridaFuente(fuente_id=fuente.id, avisos=[])
    db.add(corrida)
    db.commit()  # que quede registrada aunque después falle
    try:
        candidatos, directos = _candidatos(cfg, ctx, ciudad, corrida)
        corrida.items_vistos = len(candidatos) + len(directos)

        for ev in directos:
            _procesar_directo(ctx, ciudad, fuente, corrida, ev)

        # Notas: sacar las ya vistas y las viejas; tope por corrida
        vistas = set(db.scalars(select(ItemCrudo.url).where(ItemCrudo.fuente_id == fuente.id)))
        limite_viejo = ctx.ahora - timedelta(days=s.dias_max_nota)
        nuevos = [
            c for c in {c.url: c for c in candidatos}.values()
            if c.url not in vistas and (c.fecha_publicacion is None or c.fecha_publicacion >= limite_viejo)
        ]
        nuevos.sort(key=lambda c: c.fecha_publicacion or ctx.ahora, reverse=True)
        if len(nuevos) > s.max_items_por_corrida:
            corrida.avisos = [*corrida.avisos, f"Hay {len(nuevos)} notas nuevas; se procesan {s.max_items_por_corrida} y el resto en la próxima corrida"]
            nuevos = nuevos[: s.max_items_por_corrida]

        for c in nuevos:
            item = _guardar_item(ctx, fuente, c, corrida, limite_viejo)
            if item is not None:
                corrida.items_nuevos += 1
            db.commit()

        # Ítems que pasaron el prefiltro y todavía no se extrajeron (incluye reintentos)
        pendientes = db.scalars(
            select(ItemCrudo).where(
                ItemCrudo.fuente_id == fuente.id,
                ItemCrudo.paso_prefiltro.is_(True),
                ItemCrudo.procesado.is_(None),
                ItemCrudo.intentos < MAX_INTENTOS_LLM,
            ).order_by(ItemCrudo.id)
        ).all()
        # Si el prefiltro o el rango de días cambiaron desde que se guardaron, se vuelven
        # a aplicar antes de gastar LLM (es gratis).
        for item in list(pendientes):
            if not pasa_prefiltro(f"{item.titulo or ''}\n\n{item.texto or ''}"):
                item.paso_prefiltro, item.procesado = False, ctx.ahora
                pendientes.remove(item)
            elif item.fecha_publicacion and item.fecha_publicacion < limite_viejo:
                item.paso_prefiltro, item.procesado = False, ctx.ahora
                item.error = "Nota vieja (fuera del rango de días)"
                pendientes.remove(item)
        db.commit()
        if pendientes and ctx.extractor is None:
            corrida.avisos = [*corrida.avisos, f"{len(pendientes)} notas esperan extracción: falta ANTHROPIC_API_KEY"]
        elif pendientes:
            for item in pendientes:
                _extraer_item(ctx, ciudad, fuente, item, corrida)
                db.commit()

        corrida.ok = True
        fuente.ultimo_ok = ctx.ahora
        fuente.ultimo_error = None
    except Exception as e:  # noqa: BLE001 — una fuente rota no frena a las demás
        db.rollback()  # corrida y fuente ya estaban guardadas: solo se pierde lo no confirmado
        log.exception("Falló la fuente %s", fuente.clave)
        corrida.ok = False
        corrida.error = f"{type(e).__name__}: {e}"[:2000]
        fuente.ultimo_error = corrida.error
    corrida.fin = datetime.now(timezone.utc)
    fuente.ultima_corrida = corrida.fin
    fuente.ultimos_items = corrida.items_nuevos
    db.commit()
    return corrida


def _guardar_item(ctx: Contexto, fuente: Fuente, c: lectores.Candidato, corrida: CorridaFuente,
                  limite_viejo: datetime | None = None) -> ItemCrudo | None:
    texto, fecha = c.texto, c.fecha_publicacion
    error = None
    if texto is None:
        try:
            html = ctx.descargador.obtener(c.url)
            texto, fecha_meta = _texto_de_nota(html, c.url)
            fecha = fecha or fecha_meta
        except BloqueadoPorRobots as e:
            corrida.avisos = [*corrida.avisos, str(e)]
            return None
        except Exception as e:  # noqa: BLE001
            error = f"No se pudo bajar la nota: {e}"[:1000]
    titulo = c.titulo or ""
    cuerpo = f"{titulo}\n\n{texto or ''}".strip()
    item = ItemCrudo(
        fuente_id=fuente.id,
        url=c.url,
        titulo=c.titulo,
        texto=texto,
        fecha_publicacion=fecha,
        hash_contenido=hashlib.sha256(cuerpo.encode()).hexdigest() if texto else None,
        paso_prefiltro=pasa_prefiltro(cuerpo) if texto else False,
        error=error,
    )
    if item.paso_prefiltro and limite_viejo and fecha and fecha < limite_viejo:
        # Las secciones no dicen la fecha en la URL: recién la sabemos al bajar la nota.
        item.paso_prefiltro, item.error = False, "Nota vieja (fuera del rango de días)"
    if not item.paso_prefiltro:
        item.procesado = ctx.ahora  # descartado: no se vuelve a mirar
    else:
        corrida.items_prefiltro += 1
    ctx.db.add(item)
    ctx.db.flush()
    return item


def _extraer_item(ctx: Contexto, ciudad: Ciudad, fuente: Fuente, item: ItemCrudo, corrida: CorridaFuente) -> None:
    item.intentos += 1
    tz = ZoneInfo(ciudad.timezone)
    fecha_pub = (item.fecha_publicacion or ctx.ahora).astimezone(tz)
    try:
        respuesta, crudo = ctx.extractor.extraer(
            f"{item.titulo or ''}\n\n{item.texto or ''}".strip(),
            ciudad=ciudad.nombre,
            provincia=ciudad.provincia,
            timezone=ciudad.timezone,
            fecha_publicacion=f"{fecha_pub.date().isoformat()} ({DIAS[fecha_pub.weekday()]})",
        )
    except ExtraccionRechazada as e:
        item.error, item.procesado = str(e), ctx.ahora
        corrida.avisos = [*corrida.avisos, f"{item.url}: {e}"]
        return
    except ErrorExtraccion as e:
        item.error = str(e)  # queda sin procesar: se reintenta en la próxima corrida
        corrida.avisos = [*corrida.avisos, f"{item.url}: {e}"]
        return

    item.extraccion = crudo
    item.procesado = ctx.ahora
    item.error = None
    corrida.items_extraidos += 1
    for ex in respuesta.eventos:
        ne = _a_nuevo_evento(ex, item.url, tz, ciudad)
        if ne is None:
            continue
        r = guardar_evento(ctx.db, ciudad, fuente, item, ne, ctx.ahora)
        if r.nuevo:
            corrida.eventos_nuevos += 1
        else:
            corrida.eventos_fusionados += 1


def _a_nuevo_evento(ex: EventoExtraido, url: str, tz: ZoneInfo, ciudad: Ciudad) -> NuevoEvento | None:
    if ex.inicio is None:
        return None  # sin fecha no es agenda
    if not misma_ciudad(ex.ciudad, ciudad.nombre):
        log.info("Evento en otra ciudad (%s), se descarta: %s", ex.ciudad, ex.titulo)
        return None
    inicio = ex.inicio if ex.inicio.tzinfo else ex.inicio.replace(tzinfo=tz)
    fin = ex.fin if (ex.fin is None or ex.fin.tzinfo) else ex.fin.replace(tzinfo=tz)
    if fin is not None and fin < inicio:
        fin = None
    if not ex.hora_confirmada:
        # Nunca inventamos hora: el evento queda en la medianoche local de ese día
        inicio = inicio.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    lugar = ", ".join(p for p in (ex.lugar_nombre, ex.direccion) if p) or None
    return NuevoEvento(
        titulo=ex.titulo, inicio=inicio, fin=fin, hora_confirmada=ex.hora_confirmada,
        descripcion=ex.descripcion, lugar_texto=lugar, precio_texto=ex.precio_texto,
        gratis=ex.gratis, categoria=ex.categoria, confianza=ex.confianza, url_origen=url,
    )


def _procesar_directo(ctx: Contexto, ciudad: Ciudad, fuente: Fuente, corrida: CorridaFuente,
                      ev: lectores.EventoDirecto) -> None:
    """Eventos ya estructurados (calendario municipal): sin LLM, con dedup igual."""
    db = ctx.db
    item = db.scalar(select(ItemCrudo).where(ItemCrudo.fuente_id == fuente.id, ItemCrudo.url == ev.url))
    firma = hashlib.sha256(f"{ev.titulo}|{ev.inicio}|{ev.fin}|{ev.lugar_texto}|{ev.descripcion}".encode()).hexdigest()
    if item is not None and item.hash_contenido == firma:
        return  # sin cambios
    if item is None:
        item = ItemCrudo(fuente_id=fuente.id, url=ev.url)
        db.add(item)
        corrida.items_nuevos += 1
    item.titulo, item.texto, item.hash_contenido = ev.titulo, ev.descripcion, firma
    item.paso_prefiltro, item.procesado = True, ctx.ahora
    item.extraccion = {"directo": True, "id_externo": ev.id_externo, **ev.extra}
    db.flush()
    ne = NuevoEvento(
        titulo=ev.titulo, inicio=ev.inicio, fin=ev.fin, hora_confirmada=ev.hora_confirmada,
        descripcion=ev.descripcion, lugar_texto=ev.lugar_texto, precio_texto=None, gratis=None,
        categoria="otro", confianza=0.9,  # fuente oficial y estructurada
        url_origen=ev.url,
    )
    r = guardar_evento(db, ciudad, fuente, item, ne, ctx.ahora)
    if r.nuevo:
        corrida.eventos_nuevos += 1
    else:
        corrida.eventos_fusionados += 1
    db.commit()


# --- Todas las fuentes -------------------------------------------------------------------
def toca_correr(fuente: Fuente, cfg: ConfigFuente, ahora: datetime) -> bool:
    if not fuente.activa or not cfg.activa:
        return False
    if fuente.ultima_corrida is None:
        return True
    return ahora - fuente.ultima_corrida >= timedelta(hours=cfg.cada_horas) - timedelta(minutes=5)


def correr_todas(db: Session, configs: dict[str, ConfigFuente], solo: str | None = None,
                 forzar: bool = False) -> list[CorridaFuente]:
    s = get_settings()
    extractor = None
    if s.anthropic_api_key:
        extractor = Extractor()
    descargador = Descargador()
    ctx = Contexto(db=db, descargador=descargador, extractor=extractor)
    corridas = []
    try:
        for fuente in db.scalars(select(Fuente).where(Fuente.clave.in_(list(configs))).order_by(Fuente.id)):
            if solo and fuente.clave != solo:
                continue
            cfg = configs[fuente.clave]
            if not forzar and not toca_correr(fuente, cfg, ctx.ahora):
                continue
            log.info("Corriendo fuente %s", fuente.clave)
            corridas.append(correr_fuente(ctx, fuente, cfg))
    finally:
        descargador.close()
    return corridas
