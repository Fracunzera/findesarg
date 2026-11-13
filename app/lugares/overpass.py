"""Importación de plazas, parques y espacios verdes desde OpenStreetMap (Overpass API).

Datos © colaboradores de OpenStreetMap, licencia ODbL.
"""
import logging
from dataclasses import dataclass, field

from app.config import get_settings
from app.http import ClienteEducado

log = logging.getLogger(__name__)

TEXTO_SIN_HORARIO = "Sin horario informado (generalmente abierta)"

# Valores de access que indican que no es un espacio público
ACCESO_PRIVADO = {"private", "no", "customers"}


def armar_consulta(bbox: tuple[float, float, float, float], timeout: int = 90) -> str:
    """bbox = (sur, oeste, norte, este). `out center` da un punto también para ways/relaciones."""
    s, w, n, e = bbox
    b = f"{s},{w},{n},{e}"
    return (
        f"[out:json][timeout:{timeout}];\n"
        "(\n"
        f'  nwr["leisure"~"^(park|garden|playground)$"]({b});\n'
        f'  nwr["place"="square"]({b});\n'
        ");\n"
        "out center tags;"
    )


@dataclass
class LugarOSM:
    osm_id: str
    nombre: str | None
    tipo: str
    lat: float
    lon: float
    direccion: str | None
    horario_osm: str | None
    etiquetas: dict = field(default_factory=dict)


def clasificar(tags: dict) -> str:
    """Mapea las etiquetas OSM a nuestro `tipo` de lugar."""
    nombre = (tags.get("name") or "").strip().lower()
    leisure = tags.get("leisure")
    if tags.get("place") == "square" or nombre.startswith("plaza") or nombre.startswith("plazoleta"):
        # En Argentina muchas plazas están etiquetadas como leisure=park con nombre "Plaza ..."
        # Pero un playground llamado "Plaza de juegos" sigue siendo juegos.
        if leisure == "playground":
            return "juegos"
        return "plaza"
    if leisure == "park":
        return "parque"
    if leisure == "playground":
        return "juegos"
    if leisure == "garden":
        return "jardin"
    return "otro"


def armar_direccion(tags: dict) -> str | None:
    calle = tags.get("addr:street")
    if not calle:
        return None
    numero = tags.get("addr:housenumber")
    return f"{calle} {numero}" if numero else calle


def parsear_elementos(datos: dict) -> list[LugarOSM]:
    """Convierte la respuesta JSON de Overpass en lugares. No toca la base."""
    lugares: list[LugarOSM] = []
    for el in datos.get("elements", []):
        tags = el.get("tags") or {}
        if tags.get("access") in ACCESO_PRIVADO:
            continue
        if tags.get("leisure") == "garden" and not tags.get("name"):
            # Sin nombre casi siempre es un jardín privado o un cantero: no es un lugar para ir.
            continue
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            centro = el.get("center") or {}
            lat, lon = centro.get("lat"), centro.get("lon")
        if lat is None or lon is None:
            continue
        horario = (tags.get("opening_hours") or "").strip() or None
        lugares.append(
            LugarOSM(
                osm_id=f"{el['type']}/{el['id']}",
                nombre=(tags.get("name") or "").strip() or None,
                tipo=clasificar(tags),
                lat=lat,
                lon=lon,
                direccion=armar_direccion(tags),
                horario_osm=horario,  # nunca inventamos: si no está, queda None
                etiquetas=tags,
            )
        )
    return lugares


def descargar(bbox: tuple[float, float, float, float]) -> dict:
    """Prueba cada endpoint de Overpass en orden hasta que uno responda."""
    consulta = armar_consulta(bbox)
    ultimo_error: Exception | None = None
    with ClienteEducado(timeout=180, reintentos=3, backoff_inicial=15) as cliente:
        for url in get_settings().lista_overpass:
            try:
                log.info("Consultando Overpass en %s", url)
                resp = cliente.post(url, data={"data": consulta})
                return resp.json()
            except Exception as e:  # noqa: BLE001 — probamos el siguiente espejo
                log.warning("Falló Overpass en %s: %s", url, e)
                ultimo_error = e
    raise RuntimeError(f"Ningún endpoint de Overpass respondió: {ultimo_error}")


def texto_horario(horario_osm: str | None) -> str:
    return horario_osm or TEXTO_SIN_HORARIO
