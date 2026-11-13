"""Prueba contra la API real de Claude con las notas de ejemplo.

Solo corre si hay ANTHROPIC_API_KEY (cuesta centavos: ~7 llamadas a claude-haiku-5-5).
    docker compose run --rm web pytest tests/test_extraccion_llm.py -v
"""
import pytest

from app.config import get_settings
from app.ingesta.extractor import Extractor
from app.ingesta.pipeline import misma_ciudad
from tests.conftest import leer_nota

pytestmark = pytest.mark.skipif(not get_settings().anthropic_api_key, reason="sin ANTHROPIC_API_KEY")


@pytest.fixture(scope="module")
def extractor():
    return Extractor()


def _casos():
    import json
    from tests.conftest import NOTAS
    datos = json.loads((NOTAS / "esperado.json").read_text(encoding="utf-8"))
    datos.pop("_comentario")
    return sorted(datos.items())


@pytest.mark.parametrize("nombre,exp", _casos())
def test_extraccion_real(extractor, nombre, exp):
    r, _ = extractor.extraer(
        leer_nota(nombre), ciudad="Río Gallegos", provincia="Santa Cruz",
        timezone="America/Argentina/Rio_Gallegos", fecha_publicacion="2026-11-12 (jueves)",
    )
    llm = exp["llm"]
    evs = r.eventos
    assert len(evs) == llm["eventos"], [e.model_dump() for e in evs]
    if not evs:
        return
    e = evs[0]
    if "fecha" in llm:
        assert e.inicio.date().isoformat() == llm["fecha"]
    if "fechas" in llm:
        assert sorted(x.inicio.date().isoformat() for x in evs) == llm["fechas"]
    if "hora" in llm:
        assert e.inicio.strftime("%H:%M") == llm["hora"]
    if "hora_confirmada" in llm:
        assert e.hora_confirmada is llm["hora_confirmada"]
    if not llm.get("hora_confirmada", True):
        assert e.confianza < 0.9  # el prompt pide bajar la confianza sin hora
    if "categoria" in llm:
        assert e.categoria in llm["categoria"]
    if "gratis" in llm:
        assert e.gratis is llm["gratis"]
    if llm.get("ciudad_distinta"):
        assert not misma_ciudad(e.ciudad, "Río Gallegos")
    else:
        assert all(misma_ciudad(x.ciudad, "Río Gallegos") for x in evs)
