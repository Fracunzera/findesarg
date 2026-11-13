"""Canalización completa contra la base, con el LLM simulado (sin red)."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, text

from app.ciudades import bbox_a_wkt
from app.db import SessionLocal
from app.ingesta import lectores
from app.ingesta.extractor import parsear_respuesta
from app.ingesta.pipeline import Contexto, _extraer_item, _guardar_item, _procesar_directo, decidir_estado
from app.models import Ciudad, CorridaFuente, Evento, Fuente, ItemCrudo
from tests.conftest import FIXTURES, leer_nota

TZ = ZoneInfo("America/Argentina/Rio_Gallegos")
AHORA = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)  # jueves, fecha de publicación de las notas
SLUG = "test-ingesta-pytest"


class ExtractorFalso:
    """Devuelve la respuesta simulada de esperado.json según qué nota recibe."""

    def __init__(self, esperado):
        self.por_inicio = {leer_nota(n)[:40]: e["respuesta_simulada"] for n, e in esperado.items()}
        self.llamadas = 0

    def extraer(self, texto, **kw):
        self.llamadas += 1
        for inicio, resp in self.por_inicio.items():
            if inicio in texto:
                return parsear_respuesta(json.dumps(resp)), {"modelo": "falso", "uso": {}}
        raise AssertionError("nota desconocida")


@pytest.fixture
def db():
    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM corrida_fuente LIMIT 1"))
    except Exception:
        pytest.skip("No hay base de datos con el esquema aplicado")
    s.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    s.commit()
    yield s
    s.rollback()
    s.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    s.commit()
    s.close()


@pytest.fixture
def entorno(db, esperado):
    ciudad = Ciudad(slug=SLUG, nombre="Río Gallegos", provincia="Santa Cruz",
                    timezone="America/Argentina/Rio_Gallegos", bbox=bbox_a_wkt([-51.7, -69.4, -51.5, -69.1]))
    db.add(ciudad)
    db.flush()
    fuente = Fuente(ciudad_id=ciudad.id, clave=f"{SLUG}-fuente", nombre="Diario de prueba", tipo="html",
                    url="https://ejemplo.test/")
    db.add(fuente)
    db.flush()
    corrida = CorridaFuente(fuente_id=fuente.id, avisos=[])
    db.add(corrida)
    db.commit()
    ctx = Contexto(db=db, descargador=None, extractor=ExtractorFalso(esperado), ahora=AHORA)
    return ctx, ciudad, fuente, corrida


def _ingresar(ctx, ciudad, fuente, corrida, nombre):
    c = lectores.Candidato(url=f"https://ejemplo.test/{nombre}", titulo=None,
                           fecha_publicacion=AHORA, texto=leer_nota(nombre))
    item = _guardar_item(ctx, fuente, c, corrida)
    if item.paso_prefiltro:
        _extraer_item(ctx, ciudad, fuente, item, corrida)
    ctx.db.commit()
    return item


def _eventos(db, ciudad):
    return {e.titulo: e for e in db.scalars(select(Evento).where(Evento.ciudad_id == ciudad.id))}


def test_notas_de_ejemplo_de_punta_a_punta(entorno, esperado):
    ctx, ciudad, fuente, corrida = entorno
    for nombre in esperado:
        _ingresar(ctx, ciudad, fuente, corrida, nombre)

    evs = _eventos(ctx.db, ciudad)
    # 03 (política) no pasa el prefiltro: el LLM no se llama
    assert ctx.extractor.llamadas == len(esperado) - 1
    # 05 es en El Calafate: se descarta. 06 no tiene eventos. 01 y 01b son el mismo.
    assert not any("Calafate" in t for t in evs)
    assert len(evs) == 5, list(evs)

    recital = next(e for t, e in evs.items() if "Viento Sur" in t)
    assert recital.estado == "publicado"
    assert recital.hora_confirmada and recital.inicio.astimezone(TZ).hour == 21
    assert recital.url_origen.endswith("01_recital.txt")            # se quedó con el más completo
    assert recital.urls_alternativas == ["https://ejemplo.test/01b_recital_otro_medio.txt"]
    assert recital.slug == "viento-sur-trio-presenta-ruta-40-2026-11-14"

    feria_libro = evs["Feria del Libro Patagónico"]
    assert feria_libro.estado == "pendiente"                           # confianza 0.75
    assert not feria_libro.hora_confirmada
    assert feria_libro.inicio.astimezone(TZ).strftime("%H:%M") == "00:00"
    assert feria_libro.lugar_texto == "Complejo Cultural Santa Cruz, Ramón y Cajal 51"

    assert evs["Feria de Emprendedores"].gratis is True
    assert evs["Feria de Emprendedores"].estado == "publicado"

    items = ctx.db.scalars(select(ItemCrudo).where(ItemCrudo.fuente_id == fuente.id)).all()
    assert len(items) == len(esperado) and all(i.procesado for i in items)
    assert corrida.eventos_fusionados == 1


def test_orden_inverso_tambien_fusiona(entorno):
    ctx, ciudad, fuente, corrida = entorno
    _ingresar(ctx, ciudad, fuente, corrida, "01b_recital_otro_medio.txt")
    _ingresar(ctx, ciudad, fuente, corrida, "01_recital.txt")
    evs = list(_eventos(ctx.db, ciudad).values())
    assert len(evs) == 1
    ev = evs[0]
    assert ev.url_origen.endswith("01_recital.txt") and ev.hora_confirmada
    assert ev.urls_alternativas == ["https://ejemplo.test/01b_recital_otro_medio.txt"]
    assert ev.estado == "publicado"   # al fusionar, la confianza sube a la del mejor


def test_calendario_municipal_sin_llm(entorno):
    ctx, ciudad, fuente, corrida = entorno
    evs = lectores.leer_muni_calendario(
        (FIXTURES / "fuentes" / "muni_calendario.json").read_text(encoding="utf-8"),
        ciudad.timezone, "https://riogallegos.gob.ar/")
    for ev in evs:
        _procesar_directo(ctx, ciudad, fuente, corrida, ev)
    for ev in evs:  # segunda pasada: sin cambios, no duplica
        _procesar_directo(ctx, ciudad, fuente, corrida, ev)
    guardados = _eventos(ctx.db, ciudad)
    assert len(guardados) == 4
    assert all(e.estado == "pendiente" for e in guardados.values())  # ya pasaron (son de agosto-octubre)
    assert ctx.extractor.llamadas == 0


def test_decidir_estado():
    sab = datetime(2026, 11, 14, 21, tzinfo=TZ)
    assert decidir_estado(0.9, sab, None, True, TZ, AHORA) == "publicado"
    assert decidir_estado(0.79, sab, None, True, TZ, AHORA) == "pendiente"
    pasado = datetime(2026, 11, 10, 21, tzinfo=TZ)
    assert decidir_estado(0.95, pasado, None, True, TZ, AHORA) == "pendiente"
    # Sin hora: vale todo el día de hoy
    hoy = datetime(2026, 11, 12, 0, tzinfo=TZ)
    assert decidir_estado(0.9, hoy, None, False, TZ, AHORA) == "publicado"
    # Con fin futuro (muestra que sigue abierta)
    assert decidir_estado(0.9, pasado, datetime(2026, 11, 30, 20, tzinfo=TZ), True, TZ, AHORA) == "publicado"
