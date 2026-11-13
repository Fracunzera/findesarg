import json
from pathlib import Path

import pytest

from app.lugares import overpass

FIXTURE = Path(__file__).parent / "fixtures" / "overpass_rio_gallegos.json"


@pytest.fixture
def lugares():
    return {l.osm_id: l for l in overpass.parsear_elementos(json.loads(FIXTURE.read_text()))}


def test_consulta_incluye_las_cuatro_etiquetas_y_el_bbox():
    q = overpass.armar_consulta((-51.665, -69.345, -51.58, -69.15))
    assert 'leisure"~"^(park|garden|playground)$"' in q
    assert 'place"="square"' in q
    assert "(-51.665,-69.345,-51.58,-69.15)" in q
    assert "out center tags;" in q


def test_parsea_ways_con_center_y_nodes(lugares):
    plaza = lugares["way/236132394"]
    assert plaza.nombre == "Plaza San Martín"
    assert -51.7 < plaza.lat < -51.5 and -69.4 < plaza.lon < -69.1
    camping = lugares["node/6486063826"]
    assert camping.lat and camping.lon


def test_descarta_privados_y_jardines_sin_nombre(lugares):
    assert "way/999999001" not in lugares  # access=private
    assert "way/669573656" not in lugares  # leisure=garden sin nombre


def test_mantiene_parques_sin_nombre(lugares):
    assert lugares["way/108684902"].nombre is None
    assert lugares["way/108684902"].tipo == "parque"


@pytest.mark.parametrize(
    "tags,esperado",
    [
        ({"leisure": "park", "name": "Plaza San Martín"}, "plaza"),
        ({"leisure": "park", "name": 'Plazoleta "El Gaucho"'}, "plaza"),
        ({"place": "square"}, "plaza"),
        ({"leisure": "park", "name": "Parque Cardenal Samoré"}, "parque"),
        ({"leisure": "playground", "name": 'Plaza "Barrio Capipe"'}, "juegos"),
        ({"leisure": "playground"}, "juegos"),
        ({"leisure": "garden", "name": "Islas Malvinas"}, "jardin"),
    ],
)
def test_clasificar(tags, esperado):
    assert overpass.clasificar(tags) == esperado


def test_horario_crudo_y_direccion(lugares):
    vecinal = lugares["way/1329711818"]
    assert vecinal.horario_osm == "24/7"
    assert vecinal.direccion == "Américo Semprini 1400"


def test_sin_horario_no_se_inventa(lugares):
    plaza = lugares["way/236132394"]
    assert plaza.horario_osm is None
    assert overpass.texto_horario(plaza.horario_osm) == "Sin horario informado (generalmente abierta)"
    assert overpass.texto_horario("Mo-Su 08:00-22:00") == "Mo-Su 08:00-22:00"


def test_elemento_sin_coordenadas_se_ignora():
    datos = {"elements": [{"type": "way", "id": 1, "tags": {"leisure": "park", "name": "X"}}]}
    assert overpass.parsear_elementos(datos) == []
