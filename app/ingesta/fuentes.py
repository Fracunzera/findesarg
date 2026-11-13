"""Carga de config/fuentes.yaml y sincronización con la tabla `fuente`."""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TIPOS_FUENTE, Ciudad, Fuente

log = logging.getLogger(__name__)

RUTA_FUENTES = Path(__file__).resolve().parent.parent.parent / "config" / "fuentes.yaml"


@dataclass
class ConfigFuente:
    clave: str
    ciudad: str
    nombre: str
    tipo: str
    url: str
    cada_horas: float = 3
    activa: bool = True
    formato: str | None = None
    url_nota: str | None = None
    sitio: str | None = None
    indice: dict | list = field(default_factory=dict)

    @property
    def indices(self) -> list[dict]:
        """`indice` puede ser uno solo o una lista (ej. sitemap + secciones de respaldo)."""
        return self.indice if isinstance(self.indice, list) else ([self.indice] if self.indice else [])


def cargar_config(ruta: Path = RUTA_FUENTES) -> list[ConfigFuente]:
    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f) or {}
    salida = []
    for d in datos.get("fuentes") or []:
        c = ConfigFuente(**d)
        if c.tipo not in TIPOS_FUENTE:
            raise ValueError(f"Fuente {c.clave}: tipo inválido {c.tipo!r}")
        if c.tipo == "html":
            if not c.indices:
                raise ValueError(f"Fuente {c.clave}: falta indice")
            for i in c.indices:
                if i.get("tipo") not in ("sitemap", "enlaces") or not i.get("urls"):
                    raise ValueError(f"Fuente {c.clave}: cada indice necesita tipo (sitemap|enlaces) y urls")
                if i["tipo"] == "enlaces" and not i.get("patron"):
                    raise ValueError(f"Fuente {c.clave}: un indice de enlaces necesita patron")
        if c.tipo == "api" and c.formato not in ("muni_rg_calendario", "muni_rg_noticias"):
            raise ValueError(f"Fuente {c.clave}: formato de API desconocido {c.formato!r}")
        salida.append(c)
    claves = [c.clave for c in salida]
    if len(claves) != len(set(claves)):
        raise ValueError("Hay claves de fuente repetidas en fuentes.yaml")
    return salida


def sincronizar(db: Session, ruta: Path = RUTA_FUENTES) -> dict[str, ConfigFuente]:
    """Crea/actualiza las fuentes del YAML. Las que ya no están en el YAML quedan inactivas."""
    configs = cargar_config(ruta)
    ciudades = {c.slug: c.id for c in db.scalars(select(Ciudad))}
    existentes = {f.clave: f for f in db.scalars(select(Fuente).where(Fuente.tipo.in_(("rss", "html", "api"))))}
    for c in configs:
        if c.ciudad not in ciudades:
            raise ValueError(f"Fuente {c.clave}: la ciudad {c.ciudad!r} no existe")
        f = existentes.pop(c.clave, None)
        if f is None:
            f = Fuente(clave=c.clave)
            db.add(f)
            log.info("Fuente nueva: %s", c.nombre)
        f.ciudad_id, f.nombre, f.tipo, f.url, f.activa = ciudades[c.ciudad], c.nombre, c.tipo, c.url, c.activa
    for f in existentes.values():
        f.activa = False
    db.commit()
    return {c.clave: c for c in configs}
