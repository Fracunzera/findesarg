"""Comandos de mantenimiento.

Uso (dentro del contenedor):
    python -m app.cli sincronizar-ciudades
    python -m app.cli importar-lugares [slug-ciudad]
    python -m app.cli ingestar [--fuente CLAVE] [--forzar]
    python -m app.cli probar-extraccion ARCHIVO.txt [--fecha AAAA-MM-DD] [--ciudad slug]
"""
import argparse
import logging

from sqlalchemy import select

from app import ciudades
from app.db import SessionLocal
from app.lugares.importador import importar_ciudad
from app.models import Ciudad


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("sincronizar-ciudades", help="Carga config/ciudades.yaml en la base")
    imp = sub.add_parser("importar-lugares", help="Baja plazas y parques de OSM")
    imp.add_argument("ciudad", nargs="?", help="slug de la ciudad (por defecto, todas)")
    ing = sub.add_parser("ingestar", help="Corre la ingesta de fuentes")
    ing.add_argument("--fuente", help="clave de una sola fuente")
    ing.add_argument("--forzar", action="store_true", help="corre aunque no le toque")
    pe = sub.add_parser("probar-extraccion", help="Pasa un texto por el prefiltro y el LLM (no guarda nada)")
    pe.add_argument("archivo")
    pe.add_argument("--fecha", help="fecha de publicación (por defecto, hoy)")
    pe.add_argument("--ciudad", default="rio-gallegos")
    args = p.parse_args()

    with SessionLocal() as db:
        if args.comando == "sincronizar-ciudades":
            for c in ciudades.sincronizar(db):
                print(f"OK: {c.slug} ({c.nombre})")
        elif args.comando == "importar-lugares":
            ciudades.sincronizar(db)
            q = select(Ciudad)
            if args.ciudad:
                q = q.where(Ciudad.slug == args.ciudad)
            lista = list(db.scalars(q))
            if not lista:
                raise SystemExit(f"No existe la ciudad '{args.ciudad}'")
            for c in lista:
                print(f"{c.slug}: {importar_ciudad(db, c)}")
        elif args.comando == "ingestar":
            from app.ingesta import fuentes
            from app.ingesta.pipeline import correr_todas

            ciudades.sincronizar(db)
            configs = fuentes.sincronizar(db)
            if args.fuente and args.fuente not in configs:
                raise SystemExit(f"No existe la fuente '{args.fuente}'")
            for c in correr_todas(db, configs, solo=args.fuente, forzar=args.forzar or bool(args.fuente)):
                estado = "OK" if c.ok else "ERROR"
                print(f"[{estado}] fuente {c.fuente_id}: vistos={c.items_vistos} nuevos={c.items_nuevos} "
                      f"prefiltro={c.items_prefiltro} extraidos={c.items_extraidos} "
                      f"eventos_nuevos={c.eventos_nuevos} fusionados={c.eventos_fusionados}")
                for a in c.avisos or []:
                    print(f"   aviso: {a}")
                if c.error:
                    print(f"   error: {c.error}")
        elif args.comando == "probar-extraccion":
            import json
            from datetime import date

            from app.ingesta.extractor import Extractor
            from app.ingesta.prefiltro import pasa_prefiltro, senales

            ciudad = db.scalar(select(Ciudad).where(Ciudad.slug == args.ciudad))
            texto = open(args.archivo, encoding="utf-8").read()
            print("Prefiltro:", "PASA" if pasa_prefiltro(texto) else "NO PASA", senales(texto))
            resp, crudo = Extractor().extraer(
                texto, ciudad=ciudad.nombre, provincia=ciudad.provincia, timezone=ciudad.timezone,
                fecha_publicacion=args.fecha or date.today().isoformat(),
            )
            print(json.dumps(resp.model_dump(mode="json"), ensure_ascii=False, indent=2))
            print("Uso:", crudo["uso"])


if __name__ == "__main__":
    main()
