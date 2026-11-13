"""Descargas para la ingesta: respeta robots.txt (incluido Crawl-delay) y guarda caché en disco.

- Antes de cada URL se consulta el robots.txt del dominio (se cachea 24 h en memoria).
- Si el robots.txt pide Crawl-delay, se usa ese intervalo para el dominio.
- El HTML/XML/JSON bajado se guarda en DIR_CACHE/<sha256>.  Las notas no se vuelven a
  bajar nunca (max_edad=None); los índices (listados, RSS) sí, según max_edad.
"""
import hashlib
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import get_settings
from app.http import ClienteEducado

log = logging.getLogger(__name__)

AGENTE = "quehacer-bot"
TTL_ROBOTS = 24 * 3600


class BloqueadoPorRobots(Exception):
    pass


class Descargador:
    def __init__(self, cliente: ClienteEducado | None = None, dir_cache: str | None = None):
        self.cliente = cliente or ClienteEducado(timeout=30, reintentos=2, backoff_inicial=10)
        self.dir_cache = Path(dir_cache or get_settings().dir_cache)
        self.dir_cache.mkdir(parents=True, exist_ok=True)
        self._robots: dict[str, tuple[RobotFileParser, float]] = {}

    # --- robots.txt -------------------------------------------------------------------
    def _robots_de(self, url: str) -> RobotFileParser:
        partes = urlsplit(url)
        base = f"{partes.scheme}://{partes.netloc}"
        cache = self._robots.get(base)
        if cache and time.time() - cache[1] < TTL_ROBOTS:
            return cache[0]
        rp = RobotFileParser()
        try:
            resp = self.cliente.get(f"{base}/robots.txt")
            rp.parse(resp.text.splitlines())
        except httpx.HTTPStatusError as e:
            # Convención: 4xx = no hay robots, todo permitido; 5xx = no sabemos, no entramos.
            if e.response.status_code >= 500:
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                rp.parse([])
        retraso = rp.crawl_delay(AGENTE) or rp.crawl_delay("*")
        if retraso:
            self.cliente.fijar_intervalo(partes.hostname or "", float(retraso))
        self._robots[base] = (rp, time.time())
        return rp

    def permitido(self, url: str) -> bool:
        return self._robots_de(url).can_fetch(AGENTE, url)

    # --- caché ------------------------------------------------------------------------
    def _ruta(self, url: str) -> Path:
        return self.dir_cache / hashlib.sha256(url.encode()).hexdigest()

    def obtener(self, url: str, max_edad: float | None = None) -> str:
        """Devuelve el cuerpo (texto). max_edad en segundos; None = usar caché si existe."""
        ruta = self._ruta(url)
        meta = ruta.with_suffix(".json")
        if ruta.exists() and meta.exists():
            edad = time.time() - json.loads(meta.read_text())["guardado"]
            if max_edad is None or edad < max_edad:
                return ruta.read_text(encoding="utf-8")
        if not self.permitido(url):
            raise BloqueadoPorRobots(f"robots.txt no permite {url}")
        resp = self.cliente.get(url)
        ruta.write_text(resp.text, encoding="utf-8")
        meta.write_text(json.dumps({"url": url, "guardado": time.time(), "estado": resp.status_code}))
        return resp.text

    def close(self) -> None:
        self.cliente.close()
