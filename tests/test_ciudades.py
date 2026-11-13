import pytest

from app import ciudades


def test_config_tiene_rio_gallegos():
    conf = {c["slug"]: c for c in ciudades.cargar_config()}
    rg = conf["rio-gallegos"]
    assert rg["timezone"] == "America/Argentina/Rio_Gallegos"
    s, w, n, e = rg["bbox"]
    assert s < n and w < e


def test_bbox_a_wkt():
    wkt = ciudades.bbox_a_wkt([-51.665, -69.345, -51.58, -69.15])
    assert wkt.startswith("SRID=4326;POLYGON((-69.345 -51.665,")


def test_bbox_invertido_falla():
    with pytest.raises(ValueError):
        ciudades.bbox_a_wkt([-51.58, -69.345, -51.665, -69.15])
