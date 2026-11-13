from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ingesta.dedup import DatosDedup, es_mismo_evento, hash_dedup, normalizar

TZ = ZoneInfo("America/Argentina/Rio_Gallegos")
SAB = datetime(2026, 11, 14, 21, 0, tzinfo=TZ)


def test_normalizar():
    assert normalizar("¡Llega el FESTIVAL de Río Gallegos!") == "festival"
    assert normalizar("Peña Folklórica") == "pena folklorica"


def test_mismo_evento_con_titulos_distintos():
    a = DatosDedup('Viento Sur Trío presenta "Ruta 40"', SAB, "Teatro Municipal, Pellegrini 33")
    b = DatosDedup("VIENTO SUR TRÍO LANZA RUTA 40", SAB.replace(hour=0), "Teatro Municipal")
    assert es_mismo_evento(a, b)


def test_mismo_titulo_otro_dia_lejano_no_es_el_mismo():
    a = DatosDedup("Feria de Emprendedores", SAB, "Gimnasio Rocha")
    b = DatosDedup("Feria de Emprendedores", SAB + timedelta(days=7), "Gimnasio Rocha")
    assert not es_mismo_evento(a, b)


def test_mismo_nombre_generico_en_lugares_distintos():
    a = DatosDedup("Feria de emprendedores", SAB, "Gimnasio Rocha")
    b = DatosDedup("Feria de emprendedores locales", SAB, "Club Boxing")
    assert not es_mismo_evento(a, b)


def test_eventos_distintos_mismo_lugar():
    a = DatosDedup("Recital de Viento Sur Trío", SAB, "Teatro Municipal")
    b = DatosDedup("Obra de teatro infantil Pinocho", SAB, "Teatro Municipal")
    assert not es_mismo_evento(a, b)


def test_hash_estable_e_insensible_a_mayusculas():
    a = DatosDedup("Feria del Libro", SAB, "Complejo Cultural")
    b = DatosDedup("FERIA DEL LIBRO", SAB.astimezone(timezone.utc), "complejo cultural")
    assert hash_dedup(a, TZ) == hash_dedup(b, TZ)
