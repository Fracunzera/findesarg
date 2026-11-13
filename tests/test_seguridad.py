"""Firmas, sesión, CSRF, marca del formulario, límites y validación (sin base)."""
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from app import seguridad
from app.admin import _siguiente_seguro
from app.config import get_settings
from app.formulario import validar_evento

TZ = ZoneInfo("America/Argentina/Rio_Gallegos")
HOY = date(2026, 11, 12)


@pytest.fixture(autouse=True)
def admin(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "admin_usuario", "admin")
    monkeypatch.setattr(s, "admin_clave", "clave-de-prueba")


def test_firma_ida_y_vuelta_y_manipulacion():
    t = seguridad.firmar("hola|mundo")
    assert seguridad.verificar(t) == "hola|mundo"
    cuerpo, firma = t.rsplit(".", 1)
    assert seguridad.verificar(f"{cuerpo}x.{firma}") is None
    assert seguridad.verificar(f"{cuerpo}.{'0' * len(firma)}") is None
    assert seguridad.verificar("") is None and seguridad.verificar("sinpunto") is None


def test_credenciales():
    assert seguridad.credenciales_validas("admin", "clave-de-prueba")
    assert not seguridad.credenciales_validas("admin", "otra")
    assert not seguridad.credenciales_validas("otro", "clave-de-prueba")


def test_sin_clave_no_hay_panel(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_clave", "")
    assert not seguridad.admin_habilitado()
    assert not seguridad.credenciales_validas("admin", "")


def test_sesion_vence():
    c = seguridad.crear_sesion(ahora=1000)
    assert seguridad.sesion_valida(c, ahora=1001)
    assert not seguridad.sesion_valida(c, ahora=1000 + seguridad.DURACION_SESION + 1)
    assert not seguridad.sesion_valida(seguridad.firmar("admin|9999999999"))  # formato viejo/inválido
    assert not seguridad.sesion_valida(None)


def test_csrf_atado_a_la_sesion():
    a, b = seguridad.crear_sesion(), seguridad.crear_sesion()
    assert seguridad.csrf_valido(a, seguridad.token_csrf(a))
    assert not seguridad.csrf_valido(a, seguridad.token_csrf(b))
    assert not seguridad.csrf_valido(a, None) and not seguridad.csrf_valido(None, "x")


def test_marca_del_formulario():
    m = seguridad.marca_formulario(ahora=1000)
    assert seguridad.estado_marca(m, ahora=1001) == "rapido"
    assert seguridad.estado_marca(m, ahora=1010) == "ok"
    assert seguridad.estado_marca(m, ahora=1000 + 7 * 3600) == "vencido"
    assert seguridad.estado_marca("basura", ahora=1010) == "invalido"
    assert seguridad.estado_marca(seguridad.firmar("otra|1000"), ahora=1010) == "invalido"


def test_limite_de_intentos():
    lim = seguridad.LimiteIntentos(maximo=3, ventana=60)
    for t in (0, 1, 2):
        assert not lim.bloqueado("ip", ahora=t)
        lim.registrar("ip", ahora=t)
    assert lim.bloqueado("ip", ahora=3)
    assert not lim.bloqueado("otra", ahora=3)
    assert not lim.bloqueado("ip", ahora=61)  # se fue el primero de la ventana


@pytest.mark.parametrize("valor,esperado", [
    ("/admin/eventos?estado=publicado", "/admin/eventos?estado=publicado"),
    ("/admin/fuentes", "/admin/fuentes"),
    ("https://malo.test/admin", "/admin/eventos"),
    ("//malo.test/admin", "/admin/eventos"),
    ("/admin\\@malo.test", "/admin/eventos"),
    ("/otra", "/admin/eventos"),
    (None, "/admin/eventos"),
])
def test_siguiente_seguro(valor, esperado):
    assert _siguiente_seguro(valor) == esperado


BASE = {"titulo": "Recital de rock", "fecha": "2026-11-15", "hora": "21:30", "lugar": "Boxing Club",
        "categoria": "musica"}


def test_validar_evento_ok():
    d, err = validar_evento({**BASE, "hora_fin": "01:00", "gratis": "si", "enlace": "https://ejemplo.test/x"}, TZ, HOY)
    assert err == {}
    assert d.hora_confirmada and d.gratis is True
    assert d.inicio.isoformat() == "2026-11-15T21:30:00-03:00"
    assert d.fin.isoformat() == "2026-11-16T01:00:00-03:00"  # pasada la medianoche


def test_validar_evento_sin_hora():
    d, err = validar_evento({**BASE, "hora": ""}, TZ, HOY)
    assert err == {} and not d.hora_confirmada and d.inicio.hour == 0 and d.gratis is None


@pytest.mark.parametrize("cambio,campo", [
    ({"titulo": "ab"}, "titulo"),
    ({"titulo": "x" * 201}, "titulo"),
    ({"fecha": "2026-11-11"}, "fecha"),
    ({"fecha": "2028-01-01"}, "fecha"),
    ({"fecha": "mañana"}, "fecha"),
    ({"hora": "25:00"}, "hora"),
    ({"hora": "", "hora_fin": "23:00"}, "hora"),
    ({"lugar": ""}, "lugar"),
    ({"categoria": "rave"}, "categoria"),
    ({"enlace": "javascript:alert(1)"}, "enlace"),
    ({"enlace": "ejemplo.com"}, "enlace"),
])
def test_validar_evento_errores(cambio, campo):
    d, err = validar_evento({**BASE, **cambio}, TZ, HOY)
    assert d is None and campo in err


def test_admin_puede_cargar_fechas_pasadas():
    d, err = validar_evento({**BASE, "fecha": "2026-10-01"}, TZ, HOY, permitir_pasado=True)
    assert err == {} and d.inicio.month == 10


def test_texto_se_limpia():
    d, _ = validar_evento({**BASE, "titulo": "  Recital \x00 de   rock  "}, TZ, HOY)
    assert d.titulo == "Recital de rock"
