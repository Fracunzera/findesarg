import pytest

from app.ingesta.prefiltro import pasa_prefiltro, senales
from tests.conftest import leer_nota


def test_fixtures_de_notas(esperado):
    for nombre, exp in esperado.items():
        assert pasa_prefiltro(leer_nota(nombre)) is exp["prefiltro"], (nombre, senales(leer_nota(nombre)))


@pytest.mark.parametrize("texto", [
    "Recital de rock este sábado en el Boxing",
    "La feria de artesanos abre a las 11 en la plaza San Martín",
    "Función de títeres el 20 de noviembre",
    "Show de stand up y peña folklórica en el Centro Chileno",
])
def test_pasan(texto):
    assert pasa_prefiltro(texto)


@pytest.mark.parametrize("texto", [
    "El ministro habló en función de los datos del INDEC.",
    "La ley tendrá entrada en vigencia el 1 de enero.",
    "Detuvieron a un hombre tras un robo en el barrio Belgrano.",
    "Las muestras de sangre fueron enviadas a Buenos Aires el 3 de marzo.",
    "La encuesta muestra una caída del consumo.",
])
def test_no_pasan(texto):
    assert not pasa_prefiltro(texto), senales(texto)


def test_senales_reporta_que_encontro():
    s = senales("Este sábado a las 21 h, recital en el Teatro")
    assert "recital" in s["fuertes"] and "teatro" in s["fuertes"]
    assert s["horas"] and "este sabado" in s["dias"]
    assert s["puntaje"] == 2 + 2 + 2 + 1


@pytest.mark.parametrize("texto", [
    # Gacetillas reales de gestión que antes pasaban (títulos de Prensa Santa Cruz, nov. 2026)
    "Viva Barrio llega a Fitz Roy y Caleta Olivia con dos jornadas de atención y servicios para los vecinos el 14 de noviembre",
    "Santa Cruz fortalece las políticas de género y la articulación territorial. El encuentro se realizó el 10 de noviembre.",
    "Se realizó la presentación del programa provincial el 11 de noviembre en Casa de Gobierno.",
])
def test_gacetillas_de_gestion_no_pasan(texto):
    assert not pasa_prefiltro(texto), senales(texto)


def test_encuentro_con_hora_pasa():
    assert pasa_prefiltro("Encuentro de danzas folklóricas el sábado a las 20 en el Gimnasio Rocha")
