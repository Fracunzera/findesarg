"""Cliente HTTP "educado": se identifica, respeta un rate limit por dominio
y reintenta con backoff exponencial ante errores temporales.

Todas las requests a fuentes externas tienen que pasar por acá.
"""
import logging
import threading
import time
from urllib.parse import urlsplit

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Códigos que vale la pena reintentar (servidor saturado o caído un rato)
REINTENTABLES = {429, 502, 503, 504}


class ClienteEducado:
    def __init__(
        self,
        segundos_entre_requests: float | None = None,
        timeout: float = 60.0,
        reintentos: int = 3,
        backoff_inicial: float = 10.0,
    ):
        s = get_settings()
        self.intervalo = (
            s.segundos_entre_requests if segundos_entre_requests is None else segundos_entre_requests
        )
        self.reintentos = reintentos
        self.backoff_inicial = backoff_inicial
        self._ultimo: dict[str, float] = {}
        self._intervalo_dominio: dict[str, float] = {}  # ej. por Crawl-delay del robots.txt
        self._lock = threading.Lock()
        self.http = httpx.Client(
            headers={"User-Agent": s.user_agent},
            timeout=timeout,
            follow_redirects=True,
        )

    def fijar_intervalo(self, dominio: str, segundos: float) -> None:
        """Nunca baja del intervalo general; solo lo alarga para ese dominio."""
        self._intervalo_dominio[dominio] = max(self.intervalo, segundos)

    def _esperar_turno(self, url: str) -> None:
        dominio = urlsplit(url).hostname or ""
        with self._lock:
            ahora = time.monotonic()
            intervalo = self._intervalo_dominio.get(dominio, self.intervalo)
            espera = self._ultimo.get(dominio, 0) + intervalo - ahora
            if espera > 0:
                time.sleep(espera)
            self._ultimo[dominio] = time.monotonic()

    def request(self, metodo: str, url: str, **kwargs) -> httpx.Response:
        espera = self.backoff_inicial
        for intento in range(self.reintentos + 1):
            self._esperar_turno(url)
            try:
                resp = self.http.request(metodo, url, **kwargs)
            except httpx.TransportError as e:
                if intento == self.reintentos:
                    raise
                log.warning("Error de red con %s (%s); reintento en %.0fs", url, e, espera)
            else:
                if resp.status_code not in REINTENTABLES or intento == self.reintentos:
                    resp.raise_for_status()
                    return resp
                log.warning("%s respondió %s; reintento en %.0fs", url, resp.status_code, espera)
            time.sleep(espera)
            espera *= 2
        raise RuntimeError("inalcanzable")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
