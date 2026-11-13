"""Jinja2: el entorno de plantillas, sus filtros y globales (lo comparten todas las páginas)."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.lugares.overpass import texto_horario
from app.models import CATEGORIAS

BASE = Path(__file__).resolve().parent
TZ_POR_DEFECTO = "America/Argentina/Rio_Gallegos"

templates = Jinja2Templates(directory=BASE / "templates")

# Texto + ícono por tipo de lugar: nunca distinguimos solo por color.
TIPOS_ESPACIO_VERDE = {
    "plaza": {"etiqueta": "Plaza", "icono": "i-plaza"},
    "parque": {"etiqueta": "Parque", "icono": "i-parque"},
    "juegos": {"etiqueta": "Juegos infantiles", "icono": "i-juegos"},
    "jardin": {"etiqueta": "Jardín", "icono": "i-jardin"},
}

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")

ETIQUETAS_CATEGORIA = {
    "musica": "Música", "teatro": "Teatro", "cine": "Cine", "feria": "Feria", "deporte": "Deporte",
    "taller": "Taller", "muestra": "Muestra", "infantil": "Infantil", "gastronomia": "Gastronomía",
    "otro": "Otro",
}
assert set(ETIQUETAS_CATEGORIA) == set(CATEGORIAS)

ESTADOS_FUENTE = {
    "ok": {"etiqueta": "Funciona", "icono": "i-ok"},
    "aviso": {"etiqueta": "Funciona, con avisos", "icono": "i-aviso"},
    "error": {"etiqueta": "Con error", "icono": "i-error"},
    "nunca": {"etiqueta": "Todavía no corrió", "icono": "i-vacio"},
    "pausada": {"etiqueta": "Pausada", "icono": "i-pausa"},
}
ESTADOS_EVENTO = {
    "publicado": {"etiqueta": "Publicado", "icono": "i-ok"},
    "pendiente": {"etiqueta": "Pendiente de revisión", "icono": "i-reloj"},
    "rechazado": {"etiqueta": "Rechazado", "icono": "i-error"},
}


def fecha_larga(tz: str = TZ_POR_DEFECTO) -> str:
    """Ej.: 'jueves 12 de noviembre'. Sin depender del locale del sistema."""
    hoy = datetime.now(ZoneInfo(tz))
    return f"{DIAS[hoy.weekday()]} {hoy.day} de {MESES[hoy.month - 1]}"


def formatear_distancia(metros: float | None) -> str:
    if metros is None:
        return ""
    if metros < 1000:
        return f"a {int(round(metros, -1))} m"
    return f"a {metros / 1000:.1f} km".replace(".", ",")


def hace(momento: datetime | None) -> str:
    """'hace 5 min', 'hace 3 h', 'hace 2 días'."""
    if momento is None:
        return "nunca"
    seg = (datetime.now(momento.tzinfo) - momento).total_seconds()
    if seg < 90:
        return "recién"
    if seg < 3600:
        return f"hace {int(seg // 60)} min"
    if seg < 86400:
        return f"hace {int(seg // 3600)} h"
    dias = int(seg // 86400)
    return f"hace {dias} día" + ("s" if dias != 1 else "")


def hora_local(momento: datetime | None, tz: str = TZ_POR_DEFECTO) -> str:
    if momento is None:
        return "—"
    m = momento.astimezone(ZoneInfo(tz))
    return f"{DIAS[m.weekday()][:3]} {m.day}/{m.month} {m:%H:%M}"


def cuando_evento(ev, tz: str = TZ_POR_DEFECTO) -> str:
    """'sáb 15/11 21:00' o 'sáb 15/11 · horario a confirmar'."""
    texto = hora_local(ev.inicio, tz)
    return texto if ev.hora_confirmada else f"{texto[:-6]} · horario a confirmar"


_env = templates.env
_env.filters["distancia"] = formatear_distancia
_env.filters["hace"] = hace
_env.filters["hora_local"] = hora_local
_env.filters["cuando"] = cuando_evento
_env.globals["TIPOS"] = TIPOS_ESPACIO_VERDE
_env.globals["CATEGORIAS"] = ETIQUETAS_CATEGORIA
_env.globals["ESTADOS_FUENTE"] = ESTADOS_FUENTE
_env.globals["ESTADOS_EVENTO"] = ESTADOS_EVENTO
_env.globals["texto_horario"] = texto_horario
_env.globals["fecha_larga"] = fecha_larga
_nombre = get_settings().sitio_nombre.strip()
_env.globals["SITIO_NOMBRE"] = _nombre
# Para el logo: primera palabra en negativo, el resto al lado
_env.globals["LOGO"] = (_nombre.split(" ", 1) + [""])[:2]
