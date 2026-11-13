"""Tests contra la base real (se saltean si no hay Postgres a mano).

Correr con: docker compose run --rm web pytest
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.ciudades import bbox_a_wkt
from app.db import SessionLocal
from app.lugares import overpass
from app.lugares.importador import bbox_de_ciudad, guardar_lugares
from app.main import app
from app.models import Ciudad, Lugar

FIXTURE = Path(__file__).parent / "fixtures" / "overpass_rio_gallegos.json"
SLUG = "test-ciudad-pytest"


@pytest.fixture
def db():
    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM ciudad LIMIT 1"))
    except Exception:
        pytest.skip("No hay base de datos con el esquema aplicado")
    yield s
    s.rollback()
    s.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    s.commit()
    s.close()


@pytest.fixture
def ciudad(db):
    c = Ciudad(slug=SLUG, nombre="Ciudad de Prueba", provincia="Test",
               timezone="America/Argentina/Rio_Gallegos",
               bbox=bbox_a_wkt([-51.665, -69.345, -51.58, -69.15]))
    db.add(c)
    db.commit()
    return c


def test_bbox_ida_y_vuelta(ciudad):
    assert bbox_de_ciudad(ciudad) == pytest.approx((-51.665, -69.345, -51.58, -69.15))


def test_importar_es_idempotente_y_borra_lo_que_desaparece(db, ciudad):
    lugares = overpass.parsear_elementos(json.loads(FIXTURE.read_text()))
    r1 = guardar_lugares(db, ciudad, lugares)
    assert r1["nuevos"] == len(lugares) and r1["borrados"] == 0
    r2 = guardar_lugares(db, ciudad, lugares)
    assert r2 == {"nuevos": 0, "actualizados": len(lugares), "borrados": 0}
    r3 = guardar_lugares(db, ciudad, lugares[1:])
    assert r3["borrados"] == 1
    cant = len(db.scalars(select(Lugar).where(Lugar.ciudad_id == ciudad.id)).all())
    assert cant == len(lugares) - 1


def test_pagina_plazas(db, ciudad):
    guardar_lugares(db, ciudad, overpass.parsear_elementos(json.loads(FIXTURE.read_text())))
    cliente = TestClient(app)

    r = cliente.get(f"/{SLUG}/plazas")
    assert r.status_code == 200
    assert "Plaza San Martín" in r.text
    assert "Sin horario informado (generalmente abierta)" in r.text
    assert "Horario: 24/7" in r.text
    assert "leaflet.js" in r.text

    # Parcial HTMX filtrado por tipo y ordenado por cercanía (con campos vacíos no rompe)
    r = cliente.get(f"/{SLUG}/plazas?tipo=juegos&q=&lat=&lon=", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text
    assert "Plaza San Martín" not in r.text
    assert "Barrio Capipe" in r.text

    r = cliente.get(f"/{SLUG}/plazas?lat=-51.6226&lon=-69.2181", headers={"HX-Request": "true"})
    assert "los más cercanos primero" in r.text
    assert 'class="lugar__dist">a ' in r.text


def test_ciudad_inexistente_da_404(db):
    r = TestClient(app).get("/no-existe/plazas")
    assert r.status_code == 404
    assert "No tenemos esa ciudad" in r.text
