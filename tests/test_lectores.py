import re
from datetime import datetime

from app.ingesta import lectores
from app.ingesta.fuentes import cargar_config
from tests.conftest import FIXTURES

F = FIXTURES / "fuentes"


def test_config_de_fuentes_valida():
    configs = {c.clave: c for c in cargar_config()}
    assert {"muni-rg-calendario", "prensa-santa-cruz", "la-opinion-austral", "tiemposur"} <= set(configs)
    for c in configs.values():
        assert c.url.startswith("https://")
        for i in c.indices:
            if i.get("patron"):
                re.compile(i["patron"])


def test_rss_provincial():
    cands = lectores.leer_rss((F / "prensa_santa_cruz.rss.xml").read_text(encoding="utf-8"))
    assert len(cands) == 3
    assert all(c.url.startswith("https://noticias.santacruz.gob.ar/") for c in cands)
    assert all(c.titulo and c.fecha_publicacion and c.fecha_publicacion.tzinfo for c in cands)
    assert all(c.texto is None for c in cands)  # el resumen no alcanza: se baja la nota


def test_sitemap_de_noticias_y_patron_de_secciones():
    cands = lectores.leer_sitemap_noticias((F / "la_opinion_austral.sitemap.xml").read_text(encoding="utf-8"))
    assert len(cands) == 4
    assert all(c.titulo and isinstance(c.fecha_publicacion, datetime) for c in cands)
    patron = re.compile({c.clave: c for c in cargar_config()}["la-opinion-austral"].indices[0]["patron"])
    locales = [c for c in cands if patron.search(c.url)]
    assert len(locales) == 3
    assert not any("/argentina/" in c.url for c in locales)


def test_listado_html_saca_enlaces_unicos_que_cumplen_el_patron():
    html = (F / "listado_tiemposur.html").read_text(encoding="utf-8")
    patron = r"^https://www\.tiemposur\.com\.ar/(cultura|espectaculos)/[a-z0-9-]+$"
    cands = lectores.leer_listado(html, "https://www.tiemposur.com.ar/cultura", patron)
    urls = sorted(c.url for c in cands)
    assert urls == [
        "https://www.tiemposur.com.ar/cultura/la-orquesta-municipal-toca-en-el-teatro",
        "https://www.tiemposur.com.ar/espectaculos/llega-un-show-de-humor-al-boxing",
    ]
    orquesta = next(c for c in cands if "orquesta" in c.url)
    assert orquesta.titulo == "La Orquesta Municipal toca en el Teatro"  # no el de la foto


def test_fecha_desde_la_url():
    html = '<a href="https://www.santacruzalmomento.com.ar/2026/11/07/feria-en-rio-gallegos">Feria</a>'
    c = lectores.leer_listado(html, "https://www.santacruzalmomento.com.ar/", r"/20\d\d/")[0]
    assert c.fecha_publicacion.date().isoformat() == "2026-11-07"


def test_api_noticias_municipales():
    cands = lectores.leer_muni_noticias(
        (F / "muni_noticias.json").read_text(encoding="utf-8"), "https://riogallegos.gob.ar/noticias/{slug}")
    assert len(cands) == 1
    c = cands[0]
    assert c.url.startswith("https://riogallegos.gob.ar/noticias/")
    assert c.texto and "<p>" not in c.texto
    assert c.fecha_publicacion.tzinfo is not None


def test_api_calendario_municipal():
    evs = lectores.leer_muni_calendario(
        (F / "muni_calendario.json").read_text(encoding="utf-8"),
        "America/Argentina/Rio_Gallegos", "https://riogallegos.gob.ar/")
    assert len(evs) == 4
    por_id = {e.id_externo: e for e in evs}
    guardavida = por_id["evt-11"]
    assert guardavida.hora_confirmada and guardavida.inicio.hour == 16
    assert guardavida.inicio.utcoffset().total_seconds() == -3 * 3600
    assert guardavida.lugar_texto.startswith("Natatorio Municipal")
    talento = por_id["evt-26"]  # allDay + time_to_be_confirmed
    assert not talento.hora_confirmada and (talento.inicio.hour, talento.inicio.minute) == (0, 0)
    assert all(e.descripcion is None or len(e.descripcion) <= 320 for e in evs)
