"""Firmas HMAC, sesión del admin, tokens CSRF y límites contra abuso.

Todo con la biblioteca estándar: hay un solo administrador (usuario y clave en .env),
así que no hace falta una tabla de usuarios ni dependencias extra.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from functools import lru_cache

from fastapi import Request

from app.config import get_settings

log = logging.getLogger(__name__)

COOKIE_SESION = "qh_sesion"
DURACION_SESION = 12 * 3600  # segundos


@lru_cache
def secreto() -> bytes:
    s = get_settings().secreto
    if not s:
        log.warning("Falta SECRETO en .env: uso uno al azar (las sesiones se pierden al reiniciar)")
        return secrets.token_bytes(32)
    return s.encode()


def _hmac(texto: str) -> str:
    return hmac.new(secreto(), texto.encode(), hashlib.sha256).hexdigest()


def firmar(valor: str) -> str:
    """'valor.firma' con el valor en base64 url-safe (sin caracteres raros en cookies)."""
    codificado = base64.urlsafe_b64encode(valor.encode()).decode().rstrip("=")
    return f"{codificado}.{_hmac(codificado)}"


def verificar(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    codificado, firma = token.rsplit(".", 1)
    if not hmac.compare_digest(firma, _hmac(codificado)):
        return None
    try:
        return base64.urlsafe_b64decode(codificado + "=" * (-len(codificado) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None


# --- Sesión del admin ---------------------------------------------------------------------
def admin_habilitado() -> bool:
    return bool(get_settings().admin_clave)


def credenciales_validas(usuario: str, clave: str) -> bool:
    s = get_settings()
    if not s.admin_clave:
        return False
    # Se comparan las dos siempre (sin cortocircuito) para no filtrar cuál falló por tiempo.
    ok_usuario = hmac.compare_digest(usuario.encode(), s.admin_usuario.encode())
    ok_clave = hmac.compare_digest(clave.encode(), s.admin_clave.encode())
    return ok_usuario and ok_clave


def crear_sesion(ahora: float | None = None) -> str:
    vence = int((time.time() if ahora is None else ahora) + DURACION_SESION)
    return firmar(f"{get_settings().admin_usuario}|{vence}|{secrets.token_hex(8)}")


def sesion_valida(cookie: str | None, ahora: float | None = None) -> bool:
    valor = verificar(cookie)
    if valor is None:
        return False
    try:
        usuario, vence, _ = valor.split("|")
        return usuario == get_settings().admin_usuario and int(vence) > (time.time() if ahora is None else ahora)
    except ValueError:
        return False


def token_csrf(cookie: str) -> str:
    """Atado a la sesión: otra pestaña o sitio no lo puede adivinar."""
    return _hmac(f"csrf|{cookie}")


def csrf_valido(cookie: str | None, token: str | None) -> bool:
    return bool(cookie and token) and hmac.compare_digest(token, token_csrf(cookie))


# --- Formulario público ---------------------------------------------------------------------
SEGUNDOS_MIN_FORMULARIO = 4       # un humano tarda más que esto en completarlo
SEGUNDOS_MAX_FORMULARIO = 6 * 3600


def marca_formulario(ahora: float | None = None) -> str:
    """Token con la hora en que se mostró el formulario (contra bots que postean directo)."""
    return firmar(f"form|{int(time.time() if ahora is None else ahora)}")


def estado_marca(token: str | None, ahora: float | None = None) -> str:
    """'ok', 'rapido' (menos de unos segundos: bot), 'vencido' o 'invalido'."""
    valor = verificar(token)
    if not valor or not valor.startswith("form|"):
        return "invalido"
    try:
        edad = (time.time() if ahora is None else ahora) - int(valor.split("|", 1)[1])
    except ValueError:
        return "invalido"
    if edad < SEGUNDOS_MIN_FORMULARIO:
        return "rapido"
    return "ok" if edad <= SEGUNDOS_MAX_FORMULARIO else "vencido"


def ip_cliente(request: Request) -> str:
    # uvicorn corre con --proxy-headers: request.client ya es la IP real que manda el proxy.
    return request.client.host if request.client else "desconocida"


def hash_ip(ip: str) -> str:
    """No guardamos IPs: alcanza con poder contar envíos de la misma."""
    return _hmac(f"ip|{ip}")


class LimiteIntentos:
    """Ventana deslizante en memoria (un solo proceso de uvicorn; se resetea al reiniciar)."""

    def __init__(self, maximo: int, ventana: float):
        self.maximo, self.ventana = maximo, ventana
        self._fallos: dict[str, deque[float]] = defaultdict(deque)

    def _limpiar(self, clave: str, ahora: float) -> deque[float]:
        q = self._fallos[clave]
        while q and q[0] <= ahora - self.ventana:
            q.popleft()
        return q

    def bloqueado(self, clave: str, ahora: float | None = None) -> bool:
        return len(self._limpiar(clave, time.time() if ahora is None else ahora)) >= self.maximo

    def registrar(self, clave: str, ahora: float | None = None) -> None:
        ahora = time.time() if ahora is None else ahora
        self._limpiar(clave, ahora).append(ahora)

    def resetear(self, clave: str) -> None:
        self._fallos.pop(clave, None)


# 5 claves mal en 15 minutos bloquean esa IP por el resto de la ventana.
limite_login = LimiteIntentos(maximo=5, ventana=15 * 60)
