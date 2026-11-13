"""Tareas programadas (APScheduler, sin colas externas).

- Lugares de OSM: una vez por semana (y al arrancar si la ciudad no tiene ninguno).
- Ingesta de fuentes: cada 30 minutos se fija qué fuentes "tocan" según su cada_horas.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select, update

from app import ciudades
from app.ingesta import fuentes as fuentes_cfg
from app.ingesta.pipeline import correr_todas
from app.db import SessionLocal
from app.lugares.importador import importar_ciudad
from app.models import Ciudad, CorridaFuente, Lugar

log = logging.getLogger("worker")
TZ = ZoneInfo("America/Argentina/Rio_Gallegos")


def tarea_importar_lugares(solo_vacias: bool = False) -> None:
    with SessionLocal() as db:
        for ciudad in db.scalars(select(Ciudad)):
            if solo_vacias:
                cant = db.scalar(select(func.count()).select_from(Lugar).where(Lugar.ciudad_id == ciudad.id))
                if cant:
                    continue
            try:
                importar_ciudad(db, ciudad)
            except Exception:
                db.rollback()
                log.exception("No se pudieron importar los lugares de %s", ciudad.nombre)


def tarea_ingesta() -> None:
    with SessionLocal() as db:
        configs = fuentes_cfg.sincronizar(db)  # relee el YAML: cambios sin reiniciar
        for c in correr_todas(db, configs):
            log.info("Fuente %s: ok=%s nuevos=%s eventos=%s", c.fuente_id, c.ok, c.items_nuevos, c.eventos_nuevos)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with SessionLocal() as db:
        ciudades.sincronizar(db)
        fuentes_cfg.sincronizar(db)
        # Si el worker se cortó a mitad de una corrida, que no quede "corriendo" para siempre
        db.execute(
            update(CorridaFuente).where(CorridaFuente.fin.is_(None))
            .values(fin=func.now(), ok=False, error="Interrumpida: se reinició el worker")
        )
        db.commit()

    sched = BlockingScheduler(timezone=TZ)
    # Al arrancar: solo si no hay lugares cargados (no castigar a Overpass en cada reinicio)
    sched.add_job(tarea_importar_lugares, kwargs={"solo_vacias": True}, id="lugares_inicial")
    # Semanal: lunes 4:17 (minuto "raro" para no caer en la hora pico de Overpass)
    sched.add_job(tarea_importar_lugares, "cron", day_of_week="mon", hour=4, minute=17, id="lugares_semanal")
    sched.add_job(tarea_ingesta, "interval", minutes=30, id="ingesta", next_run_time=datetime.now(TZ),
                  max_instances=1, coalesce=True)
    log.info("Worker andando")
    sched.start()


if __name__ == "__main__":
    main()
