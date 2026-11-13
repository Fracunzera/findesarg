"""Guarda en la base los lugares bajados de OSM (upsert por osm_id)."""
import logging

from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.lugares import overpass
from app.models import Ciudad, Evento, Lugar

log = logging.getLogger(__name__)


def bbox_de_ciudad(ciudad: Ciudad) -> tuple[float, float, float, float]:
    """Devuelve (sur, oeste, norte, este)."""
    bbox = ciudad.bbox
    if isinstance(bbox, str):  # recién asignado y todavía no releído de la base
        bbox = WKTElement(bbox.split(";", 1)[-1], srid=4326)
    minx, miny, maxx, maxy = to_shape(bbox).bounds
    return (miny, minx, maxy, maxx)


def guardar_lugares(db: Session, ciudad: Ciudad, lugares: list[overpass.LugarOSM]) -> dict:
    existentes = {
        l.osm_id: l
        for l in db.scalars(select(Lugar).where(Lugar.ciudad_id == ciudad.id, Lugar.origen == "osm"))
    }
    nuevos = actualizados = 0
    vistos: set[str] = set()
    for l in lugares:
        vistos.add(l.osm_id)
        geom = f"SRID=4326;POINT({l.lon} {l.lat})"
        fila = existentes.get(l.osm_id)
        if fila is None:
            db.add(
                Lugar(
                    ciudad_id=ciudad.id, origen="osm", osm_id=l.osm_id, nombre=l.nombre,
                    tipo=l.tipo, geom=geom, direccion=l.direccion,
                    horario_osm=l.horario_osm, etiquetas_osm=l.etiquetas,
                )
            )
            nuevos += 1
        else:
            fila.nombre, fila.tipo, fila.geom = l.nombre, l.tipo, geom
            fila.direccion, fila.horario_osm, fila.etiquetas_osm = l.direccion, l.horario_osm, l.etiquetas
            actualizados += 1

    # Lo que ya no está en OSM se borra, salvo que algún evento lo use.
    desaparecidos = set(existentes) - vistos
    usados = set(db.scalars(select(Evento.lugar_id).where(Evento.lugar_id.is_not(None))))
    a_borrar = [existentes[o].id for o in desaparecidos if existentes[o].id not in usados]
    if a_borrar:
        db.execute(delete(Lugar).where(Lugar.id.in_(a_borrar)))
    db.commit()
    resumen = {"nuevos": nuevos, "actualizados": actualizados, "borrados": len(a_borrar)}
    log.info("Lugares de %s: %s", ciudad.nombre, resumen)
    return resumen


def importar_ciudad(db: Session, ciudad: Ciudad) -> dict:
    datos = overpass.descargar(bbox_de_ciudad(ciudad))
    return guardar_lugares(db, ciudad, overpass.parsear_elementos(datos))
