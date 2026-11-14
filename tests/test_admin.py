"""Formulario público y panel /admin contra la base real (se saltean si no hay Postgres)."""
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app import seguridad
from app.ciudades import bbox_a_wkt
from app.config import get_settings
from app.db import SessionLocal
from app.ingesta.pipeline import NuevoEvento, guardar_evento
from app.main import app
from app.models import Ciudad, Evento, Fuente

SLUG = "test-admin-pytest"
TZ = ZoneInfo("America/Argentina/Rio_Gallegos")


@pytest.fixture(autouse=True)
def config(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "admin_usuario", "admin")
    monkeypatch.setattr(s, "admin_clave", "clave-de-prueba")
    monkeypatch.setattr(s, "max_envios_por_hora", 3)
    seguridad.limite_login.resetear("testclient")


@pytest.fixture
def db():
    try:
        s = SessionLocal()
        s.execute(text("SELECT contacto FROM evento LIMIT 1"))
    except Exception:
        pytest.skip("No hay base de datos con el esquema 0003 aplicado")
    s.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    s.commit()
    yield s
    s.rollback()
    s.execute(delete(Ciudad).where(Ciudad.slug == SLUG))
    s.commit()
    s.close()


@pytest.fixture
def ciudad(db):
    c = Ciudad(slug=SLUG, nombre="Río Gallegos", provincia="Santa Cruz", timezone=str(TZ),
               bbox=bbox_a_wkt([-51.7, -69.4, -51.5, -69.1]))
    db.add(c)
    db.commit()
    return c


def _manana() -> str:
    return (datetime.now(TZ).date() + timedelta(days=1)).isoformat()


def _datos(**extra) -> dict:
    return {"titulo": "Peña folklórica de prueba", "fecha": _manana(), "hora": "21:00",
            "lugar": "Club Hispano", "categoria": "musica", "contacto": "peña@ejemplo.test",
            "marca": seguridad.marca_formulario(ahora=time.time() - 30), **extra}


def _eventos(db, ciudad):
    db.expire_all()
    return db.scalars(select(Evento).where(Evento.ciudad_id == ciudad.id).order_by(Evento.id)).all()


def _login(cliente) -> str:
    r = cliente.post("/admin/entrar", data={"usuario": "admin", "clave": "clave-de-prueba", "siguiente": "/admin/eventos"},
                     follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/eventos"
    r = cliente.get("/admin/eventos")
    return re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)


# --- Formulario público -----------------------------------------------------------------------
def test_formulario_crea_pendiente(db, ciudad):
    c = TestClient(app)
    r = c.get(f"/{SLUG}/cargar")
    assert r.status_code == 200 and 'name="marca"' in r.text and "sitio_web" in r.text

    r = c.post(f"/{SLUG}/cargar", data=_datos(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/{SLUG}/cargar/gracias"
    [ev] = _eventos(db, ciudad)
    assert ev.estado == "pendiente" and ev.hora_confirmada and ev.contacto == "peña@ejemplo.test"
    assert ev.ip_hash and ev.ip_hash != "testclient"
    assert db.get(Fuente, ev.fuente_id).tipo == "formulario"
    assert c.get(f"/{SLUG}/cargar/gracias").status_code == 200


def test_formulario_errores_se_muestran_y_conservan_datos(db, ciudad):
    r = TestClient(app).post(f"/{SLUG}/cargar", data=_datos(titulo="ab", lugar=""))
    assert r.status_code == 422
    assert "Revisá estos 2 datos" in r.text
    assert 'aria-invalid="true"' in r.text
    assert "Club Hispano" not in r.text and "Peña folklórica" not in r.text  # los inválidos...
    assert "peña@ejemplo.test" in r.text                                       # ...pero el resto queda
    assert _eventos(db, ciudad) == []


def test_formulario_escapa_html(db, ciudad):
    r = TestClient(app).post(f"/{SLUG}/cargar", data=_datos(titulo="<script>alert(1)</script>", lugar=""))
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


@pytest.mark.parametrize("extra", [
    {"sitio_web": "http://spam.test"},                               # campo trampa
    {"marca": seguridad.marca_formulario()},                          # demasiado rápido
    {"marca": "inventada"},                                           # sin marca válida
])
def test_formulario_bots_se_descartan_en_silencio(db, ciudad, extra):
    r = TestClient(app).post(f"/{SLUG}/cargar", data=_datos(**extra), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/gracias")
    assert _eventos(db, ciudad) == []


def test_formulario_vencido_pide_reenviar(db, ciudad):
    r = TestClient(app).post(f"/{SLUG}/cargar", data=_datos(marca=seguridad.marca_formulario(ahora=time.time() - 7 * 3600)))
    assert r.status_code == 400 and "venció" in r.text and "Club Hispano" in r.text


def test_formulario_limite_por_ip(db, ciudad):
    c = TestClient(app)
    for i in range(3):
        assert c.post(f"/{SLUG}/cargar", data=_datos(titulo=f"Evento número {i}"), follow_redirects=False).status_code == 303
    r = c.post(f"/{SLUG}/cargar", data=_datos(titulo="Uno de más"))
    assert r.status_code == 429
    assert len(_eventos(db, ciudad)) == 3


# --- Panel ------------------------------------------------------------------------------------
def test_panel_pide_login(db):
    c = TestClient(app)
    for ruta in ("/admin", "/admin/eventos", "/admin/fuentes", "/admin/eventos/1"):
        r = c.get(ruta, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/admin/entrar"), ruta
    r = c.get("/estado/fuentes", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/admin/fuentes"


def test_panel_deshabilitado_sin_clave(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_clave", "")
    r = TestClient(app).get("/admin/entrar")
    assert r.status_code == 503 and "ADMIN_CLAVE" in r.text


def test_login_incorrecto_y_bloqueo(db):
    c = TestClient(app)
    for _ in range(5):
        r = c.post("/admin/entrar", data={"usuario": "admin", "clave": "mal"})
        assert r.status_code == 401
    r = c.post("/admin/entrar", data={"usuario": "admin", "clave": "clave-de-prueba"})
    assert r.status_code == 429  # bloqueado aunque ahora sea la clave buena


def test_cookie_de_sesion(db):
    c = TestClient(app)
    r = c.post("/admin/entrar", data={"usuario": "admin", "clave": "clave-de-prueba"}, follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/admin" in cookie


def test_moderar_aprobar_rechazar_y_editar(db, ciudad):
    c = TestClient(app)
    c.post(f"/{SLUG}/cargar", data=_datos())
    [ev] = _eventos(db, ciudad)
    csrf = _login(c)

    r = c.get("/admin/eventos")
    assert "Peña folklórica de prueba" in r.text and "peña@ejemplo.test" in r.text

    # Sin CSRF no hace nada
    r = c.post(f"/admin/eventos/{ev.id}/estado", data={"accion": "publicar"})
    assert r.status_code == 403
    assert _eventos(db, ciudad)[0].estado == "pendiente"

    # Con HTMX devuelve solo la fila
    r = c.post(f"/admin/eventos/{ev.id}/estado", data={"accion": "publicar", "csrf": csrf}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text and "Publicado" in r.text
    ev = _eventos(db, ciudad)[0]
    assert ev.estado == "publicado" and ev.moderado is not None

    # Sin JS redirige de vuelta
    r = c.post(f"/admin/eventos/{ev.id}/estado", data={"accion": "rechazar", "csrf": csrf,
               "volver": "/admin/eventos?estado=publicado"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/eventos?estado=publicado"
    assert _eventos(db, ciudad)[0].estado == "rechazado"

    # Editar
    r = c.get(f"/admin/eventos/{ev.id}")
    assert r.status_code == 200 and 'value="Club Hispano"' in r.text
    r = c.post(f"/admin/eventos/{ev.id}", data={**_datos(titulo="Peña corregida", hora="", precio="$3000"),
                                                "estado": "publicado", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    ev = _eventos(db, ciudad)[0]
    assert (ev.titulo, ev.hora_confirmada, ev.precio_texto, ev.estado) == ("Peña corregida", False, "$3000", "publicado")

    r = c.post(f"/admin/eventos/{ev.id}", data={**_datos(titulo=""), "estado": "publicado", "csrf": csrf})
    assert r.status_code == 422

    assert c.get("/admin/fuentes").status_code == 200

    r = c.post("/admin/salir", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/admin/eventos", follow_redirects=False).status_code == 303


# --- Interacción con la ingesta ---------------------------------------------------------------
def _nuevo(titulo="Peña folklórica de prueba", confianza=0.95) -> NuevoEvento:
    inicio = datetime.combine(datetime.now(TZ).date() + timedelta(days=1), datetime.min.time(), tzinfo=TZ).replace(hour=21)
    return NuevoEvento(titulo=titulo, inicio=inicio, fin=None, hora_confirmada=True, descripcion="De un diario",
                       lugar_texto="Club Hispano", precio_texto=None, gratis=None, categoria="musica",
                       confianza=confianza, url_origen="https://diario.test/nota")


def _fuente_diario(db, ciudad) -> Fuente:
    f = Fuente(ciudad_id=ciudad.id, clave=f"{SLUG}-diario", nombre="Diario", tipo="html", url="https://diario.test/")
    db.add(f)
    db.commit()
    return f


def test_ingesta_no_publica_lo_del_formulario(db, ciudad):
    TestClient(app).post(f"/{SLUG}/cargar", data=_datos())
    r = guardar_evento(db, ciudad, _fuente_diario(db, ciudad), None, _nuevo())
    db.commit()
    assert r.nuevo  # no se fusiona con el envío anónimo pendiente
    estados = sorted(e.estado for e in _eventos(db, ciudad))
    assert estados == ["pendiente", "publicado"]


def test_ingesta_respeta_lo_moderado(db, ciudad):
    fuente = _fuente_diario(db, ciudad)
    r = guardar_evento(db, ciudad, fuente, None, _nuevo())
    ev = r.evento
    ev.estado, ev.moderado, ev.descripcion = "rechazado", datetime.now(timezone.utc), "Corregida a mano"
    db.commit()

    otro = _nuevo(titulo="Peña folklórica de prueba")
    otro.url_origen, otro.precio_texto = "https://otro.test/nota", "$5000"  # más completo
    r2 = guardar_evento(db, ciudad, fuente, None, otro)
    db.commit()
    assert not r2.nuevo and r2.evento.id == ev.id          # no reaparece como pendiente
    ev = _eventos(db, ciudad)[0]
    assert ev.estado == "rechazado" and ev.descripcion == "Corregida a mano" and ev.precio_texto is None
    assert "https://otro.test/nota" in ev.urls_alternativas


# --- Notas para revisar (sin LLM) -------------------------------------------------------------
def _nota(db, ciudad, texto, titulo="El Club Miramar celebra su aniversario con una peña"):
    from app.models import ItemCrudo
    f = _fuente_diario(db, ciudad)
    it = ItemCrudo(fuente_id=f.id, url=f"https://diario.test/nota-{time.time_ns()}", titulo=titulo, texto=texto,
                   fecha_publicacion=datetime.now(timezone.utc), paso_prefiltro=True)
    db.add(it)
    db.commit()
    return it


def test_notas_sugerir_y_crear_evento(db, ciudad):
    manana = datetime.now(TZ).date() + timedelta(days=1)
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
             "octubre", "noviembre", "diciembre"]
    texto = (f"La peña será el {manana.day} de {meses[manana.month - 1]} a las 21 horas en el Gimnasio Rocha. "
             "Habrá música en vivo y la entrada es libre.")
    it = _nota(db, ciudad, texto)
    c = TestClient(app)
    csrf = _login(c)

    r = c.get("/admin/notas")
    assert r.status_code == 200 and it.titulo in r.text
    r = c.get(f"/admin/notas/{it.id}")
    assert f'value="{manana.isoformat()}"' in r.text and 'value="21:00"' in r.text and "Gimnasio Rocha" in r.text

    datos = {"csrf": csrf, "titulo": "Peña del Club Miramar", "fecha": manana.isoformat(), "hora": "21:00",
             "lugar": "Gimnasio Rocha", "categoria": "musica", "gratis": "si", "enlace": it.url, "estado": "publicado"}
    assert c.post(f"/admin/notas/{it.id}", data={**datos, "csrf": "mal"}).status_code == 403
    r = c.post(f"/admin/notas/{it.id}", data=datos, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/notas?ok=")
    ev = _eventos(db, ciudad)[-1]
    assert ev.estado == "publicado" and ev.item_crudo_id == it.id and ev.moderado is not None
    db.refresh(it)
    assert it.procesado is not None and it.extraccion["eventos"] == [ev.id]
    assert c.get(f"/{SLUG}").text.count("Peña del Club Miramar") >= 1  # ya sale en la agenda


def test_notas_descartar(db, ciudad):
    it = _nota(db, ciudad, "Se presentó el informe de gestión.")
    c = TestClient(app)
    csrf = _login(c)
    r = c.post(f"/admin/notas/{it.id}/descartar", data={"csrf": csrf}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text == ""
    db.refresh(it)
    assert it.procesado is not None and "Descartada" in it.error
    assert it.titulo not in c.get("/admin/notas?ver=todas").text
