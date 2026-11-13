"""Sincroniza config/ciudades.yaml con la tabla `ciudad`."""
import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ciudad

log = logging.getLogger(__name__)

RUTA_CIUDADES = Path(__file__).resolve().parent.parent / "config" / "ciudades.yaml"


def bbox_a_wkt(bbox: list[float]) -> str:
    s, w, n, e = bbox
    if not (s < n and w < e):
        raise ValueError(f"bbox inválido (tiene que ser [sur, oeste, norte, este]): {bbox}")
    return f"SRID=4326;POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def cargar_config(ruta: Path = RUTA_CIUDADES) -> list[dict]:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)["ciudades"]


def sincronizar(db: Session, ruta: Path = RUTA_CIUDADES) -> list[Ciudad]:
    resultado = []
    for c in cargar_config(ruta):
        ciudad = db.scalar(select(Ciudad).where(Ciudad.slug == c["slug"]))
        if ciudad is None:
            ciudad = Ciudad(slug=c["slug"])
            db.add(ciudad)
            log.info("Ciudad nueva: %s", c["nombre"])
        ciudad.nombre = c["nombre"]
        ciudad.provincia = c["provincia"]
        ciudad.timezone = c["timezone"]
        ciudad.bbox = bbox_a_wkt(c["bbox"])
        resultado.append(ciudad)
    db.commit()
    for c in resultado:
        db.refresh(c)  # para que bbox vuelva como geometría y no como el texto que asignamos
    return resultado
