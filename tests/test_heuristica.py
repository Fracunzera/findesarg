"""Detección de fechas, lugar y categoría sin LLM."""
from datetime import date, time

from app.ingesta.heuristica import adivinar_categoria, adivinar_lugar, detectar_fechas, sugerir

# 2026-11-12 es jueves
PUB = date(2026, 11, 12)


def fechas(texto, pub=PUB):
    return [(f.dia, f.hora) for f in detectar_fechas(texto, pub)]


def test_fecha_larga_con_hora():
    t = "La banda se presentará el sábado 14 de noviembre a las 21 horas en el Boxing Club."
    assert fechas(t) == [(date(2026, 11, 14), time(21, 0))]


def test_formas_de_hora():
    assert fechas("El 20 de noviembre desde las 18.30 hs.") == [(date(2026, 11, 20), time(18, 30))]
    assert fechas("Será el 20 de noviembre, 21:00.") == [(date(2026, 11, 20), time(21, 0))]
    assert fechas("Viernes 20 de noviembre, 22 hs.") == [(date(2026, 11, 20), time(22, 0))]
    assert fechas("El 20 de noviembre a partir de las 20.") == [(date(2026, 11, 20), time(20, 0))]


def test_sin_hora():
    assert fechas("La feria será el 1° de diciembre en la plaza.") == [(date(2026, 12, 1), None)]


def test_precios_no_son_horas():
    assert fechas("El 20 de noviembre. Entradas a $15.000 y 12.500 en puerta.") == [(date(2026, 11, 20), None)]


def test_relativas():
    assert fechas("Este sábado habrá un recital a las 21.") == [(date(2026, 11, 14), time(21, 0))]
    assert fechas("El próximo jueves, taller de cerámica.") == [(date(2026, 11, 19), None)]
    assert fechas("Hoy a las 19 se presenta el libro.") == [(PUB, time(19, 0))]
    assert fechas("Mañana desde las 10 habrá feria.") == [(date(2026, 11, 13), time(10, 0))]
    assert fechas("Por la mañana se trabajó en la plaza.") == []


def test_dia_con_numero_sin_mes():
    assert fechas("Será el sábado 14 a las 20.") == [(date(2026, 11, 14), time(20, 0))]
    assert fechas("Será el martes 1 a las 20.", date(2026, 11, 25)) == [(date(2026, 12, 1), time(20, 0))]


def test_numerica():
    assert fechas("Será el viernes 20/11 a las 22.") == [(date(2026, 11, 20), time(22, 0))]


def test_cambio_de_anio():
    assert fechas("Vuelve el 10 de enero.", date(2026, 12, 20)) == [(date(2027, 1, 10), None)]


def test_pasado_y_viejas_no():
    assert fechas("El festival se realizó el 8 de noviembre con gran convocatoria.") == []
    assert fechas("Como el 3 de noviembre, hubo mucha gente.") == []  # antes de la nota


def test_varias_fechas_cada_una_con_su_hora():
    t = "Las funciones serán el viernes 20 de noviembre a las 21 y el sábado 21 de noviembre a las 18."
    assert fechas(t) == [(date(2026, 11, 20), time(21, 0)), (date(2026, 11, 21), time(18, 0))]


def test_hora_de_otra_oracion_no_se_mezcla_si_hay_varias():
    t = "Habrá shows el 20 de noviembre y el 21 de noviembre. La apertura es a las 19."
    assert fechas(t) == [(date(2026, 11, 20), None), (date(2026, 11, 21), None)]


def test_lugar():
    assert adivinar_lugar("Será en el Complejo Cultural Santa Cruz a las 21.") == "Complejo Cultural Santa Cruz"
    assert adivinar_lugar("La muestra se puede ver en el Museo Padre Molina, de nuestra ciudad.") == "Museo Padre Molina"
    assert adivinar_lugar("Se presentó un proyecto de ley.") is None


def test_categoria():
    assert adivinar_categoria("Recital de rock en el Boxing") == "musica"
    assert adivinar_categoria("Feria de emprendedores navideña") == "feria"
    assert adivinar_categoria("Torneo de fútbol infantil") in ("deporte", "infantil")
    assert adivinar_categoria("Sesión del Concejo Deliberante") == "otro"


def test_sugerir():
    s = sugerir("Peña folklórica en el Club Boxing",
                "El club invita a la peña folklórica del sábado 14 de noviembre a las 21 en el Boxing Club.", PUB)
    assert [(f.dia, f.hora) for f in s.fechas] == [(date(2026, 11, 14), time(21, 0))]
    assert s.lugar == "Boxing Club" and s.categoria == "musica"
