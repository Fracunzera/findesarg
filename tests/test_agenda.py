"""Agenda pública: agrupado por día (sin base) y páginas (con base)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.agenda import agrupar, armar_ics, dias_del_finde, vigente_hasta
from app.ciudades import bbox_a_wkt
from app.db import SessionLocal
from app.main import app
from app.models import Ciudad, Evento

TZ = ZoneInfo("America/Argentina/Rio_Gallegos")
SLUG = "test-agenda-pytest"


def L(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


@dataclass
class Ev:
    titulo: str
    inicio: datetime
    fin: datetime | None = None
    hora_confirmada: bool = True


# 2026-11-12 es jueves
@pytest.mark.parametrize("hoy,esperado", [
    (date(2026, 11, 9), [13, 14, 15]),   # lunes → vie-dom de esta semana
    (date(2026, 11, 12), [13, 14, 15]),  # jueves
    (date(2026, 11, 13), [14, 15]),      # viernes: el resto del finde
    (date(2026, 11, 14), [15]),          # sábado
    (date(2026, 11, 15), [20, 21, 22]),  # domingo: el que viene
])
def test_dias_del_finde(hoy, esperado):
    assert [d.day for d in dias_del_finde(hoy)] == esperado


def test_vigente_hasta():
    assert vigente_hasta(L(2026, 11, 12, 21), None, True, TZ) == L(2026, 11, 13, 0)  # +3 h
    assert vigente_hasta(L(2026, 11, 12), None, False, TZ) == L(2026, 11, 13)        # todo el día
    assert vigente_hasta(L(2026, 11, 10, 10), L(2026, 11, 20, 18), True, TZ) == L(2026, 11, 20, 18)


def test_agrupar():
    ahora = L(2026, 11, 12, 15)  # jueves 15 h
    evs = [
        Ev("Ya pasó a la mañana", L(2026, 11, 12, 10)),
        Ev("Esta noche", L(2026, 11, 12, 21)),
        Ev("Hoy sin hora", L(2026, 11, 12), hora_confirmada=False),
        Ev("Muestra en curso", L(2026, 11, 1, 10), fin=L(2026, 11, 30, 19)),
        Ev("Sábado", L(2026, 11, 14, 20)),
        Ev("Martes", L(2026, 11, 17, 19)),
        Ev("Diciembre", L(2026, 12, 5, 19)),
        Ev("Muy lejos", L(2027, 3, 1, 19)),
        Ev("Ayer", L(2026, 11, 11, 20)),
    ]
    g = {x.clave: [e.titulo for e in x.eventos] for x in agrupar(evs, ahora.astimezone(timezone.utc), TZ)}
    assert g["hoy"] == ["Muestra en curso", "Hoy sin hora", "Esta noche"]
    assert g["finde"] == ["Sábado"]
    assert g["semana"] == ["Martes"]
    assert g["despues"] == ["Diciembre"]


def test_agrupar_ordena_semana_antes_que_finde_el_domingo():
    ahora = L(2026, 11, 15, 12)  # domingo: el finde es el que viene, la semana va antes
    grupos = agrupar([Ev("Lunes", L(2026, 11, 16, 20)), Ev("Viernes", L(2026, 11, 20, 20))], ahora, TZ)
    assert [g.clave for g in grupos] == ["hoy", "semana", "finde", "despues"]


@dataclass
class EvIcs(Ev):
    id: int = 7
    descripcion: str | None = "Banda local; entrada, libre"
    lugar_texto: str | None = "Boxing Club"


def test_ics():
    c = Ciudad(slug="rio-gallegos", nombre="Río Gallegos", timezone=str(TZ))
    ics = armar_ics(EvIcs("Recital, con comas; y punto y coma", L(2026, 11, 14, 21)), c, "https://x.test/e", L(2026, 11, 12))
    assert "DTSTART:20261115T000000Z" in ics and "DTEND:20261115T030000Z" in ics
    assert r"SUMMARY:Recital\, con comas\; y punto y coma" in ics
    assert "LOCATION:Boxing Club\\, Río Gallegos" in ics
    assert all(len(l.encode()) <= 75 for l in ics.split("\r\n"))
    sin_hora = armar_ics(EvIcs("Feria", L(2026, 11, 14), hora_confirmada=False), c, "https://x.test/e", L(2026, 11, 12))
    assert "DTSTART;VALUE=DATE:20261114" in sin_hora and "DTEND;VALUE=DATE:20261115" in sin_hora
    assert "Horario a confirmar" in sin_hora


# --- Con base ---------------------------------------------------------------------------------
@pytest.fixture
def ciudad():
    try:
        db = SessionLocal()
        db.execute(text("SELECT moderado FROM evento LIMIT 1"))
    except Exception:
        pytest.skip("No hay base de datos con el esquema aplicado")
    db.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    c = Ciudad(slug=SLUG, nombre="Río Gallegos", provincia="Santa Cruz", timezone=str(TZ),
               bbox=bbox_a_wkt([-51.7, -69.4, -51.5, -69.1]))
    db.add(c)
    db.flush()
    manana = datetime.now(TZ).replace(hour=21, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for i, (titulo, estado, cat, gratis) in enumerate([
        ("Recital publicado", "publicado", "musica", False),
        ("Feria gratis publicada", "publicado", "feria", True),
        ("Pendiente secreto", "pendiente", "musica", None),
        ("Rechazado secreto", "rechazado", "musica", None),
    ]):
        db.add(Evento(ciudad_id=c.id, slug=f"{SLUG}-{i}", titulo=titulo, inicio=manana, hora_confirmada=True,
                      lugar_texto="Boxing Club", categoria=cat, gratis=gratis, estado=estado, confianza=0.9,
                      urls_alternativas=[], descripcion="Una descripción corta."))
    db.commit()
    yield c
    db.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    db.commit()
    db.close()


def test_portada_muestra_solo_publicados(ciudad):
    r = TestClient(app).get(f"/{SLUG}")
    assert r.status_code == 200
    assert "Recital publicado" in r.text and "Feria gratis publicada" in r.text
    assert "secreto" not in r.text


def test_portada_filtros(ciudad):
    c = TestClient(app)
    r = c.get(f"/{SLUG}?cat=feria")
    assert "Feria gratis publicada" in r.text and "Recital publicado" not in r.text
    r = c.get(f"/{SLUG}?gratis=1")
    assert "Feria gratis publicada" in r.text and "Recital publicado" not in r.text
    r = c.get(f"/{SLUG}?cat=teatro")
    assert "No hay nada con ese filtro" in r.text
    assert c.get(f"/{SLUG}?cat=<script>").status_code == 200  # categoría inválida = sin filtro


def test_detalle_y_calendario(ciudad):
    c = TestClient(app)
    r = c.get(f"/{SLUG}/evento/{SLUG}-0")
    assert r.status_code == 200 and "Recital publicado" in r.text and '"@type": "Event"' in r.text
    r = c.get(f"/{SLUG}/evento/{SLUG}-0/calendario.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in r.text
    for oculto in (f"{SLUG}-2", f"{SLUG}-3", "no-existe"):
        assert c.get(f"/{SLUG}/evento/{oculto}").status_code == 404
