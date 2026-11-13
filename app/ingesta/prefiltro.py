"""Prefiltro barato antes del LLM.

Descarta las notas sin señales de agenda (recital, feria, "este sábado", "21 h", etc.) para
no gastar llamadas en notas de política, policiales o gacetillas de gestión.
Funciona por puntaje: palabras fuertes, palabras débiles, hora concreta y día concreto.
Es generoso a propósito: el filtro fino lo hace el LLM.
"""
import re
import unicodedata


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


# Palabras FUERTES: casi siempre indican una actividad a la que se puede ir (2 puntos c/u).
FUERTES = [
    r"recital(?:es)?", r"shows?", r"ferias?", r"taller(?:es)?", r"funcion(?:es)?", r"entradas?",
    r"conciertos?", r"festival(?:es)?", r"obras? de teatro", r"teatro", r"cine", r"peliculas?",
    r"proyeccion(?:es)?", r"exposicion(?:es)?", r"expo", r"muestras?", r"espectaculos?",
    r"milongas?", r"penas? folkloricas?", r"stand ?up", r"maraton", r"torneo", r"degustacion",
    r"patio gastronomico", r"food ?trucks?", r"kermesse", r"bingo", r"city ?tour",
    r"visita guiada", r"recorrido guiado", r"noche de los museos", r"desfile", r"a la gorra",
    r"entrada libre", r"gratis", r"gratuit[ao]s?", r"bono contribucion", r"anticipadas?",
    r"boleteria", r"se presentara(?:n)?", r"tocara(?:n)?", r"subira(?:n)? al escenario",
]

# Palabras DÉBILES: aparecen en agenda, pero también en gacetillas de gestión (1 punto c/u).
DEBILES = [
    r"jornadas?", r"encuentros?", r"presentacion(?:es)?", r"celebracion", r"fiestas?",
    r"festejos?", r"charlas?", r"conversatorio", r"bailes?", r"danzas?", r"peatonal",
    r"carrera", r"campeonato", r"actuacion(?:es)?", r"agenda", r"artistas?", r"musica",
]

# Hora concreta (2 puntos): "a las 21", "21 h", "21:30 hs", "desde las 18".
HORAS = [
    r"\ba (?:partir )?de las \d{1,2}\b",
    r"\bdesde las \d{1,2}\b",
    r"\ba las \d{1,2}(?:[:.]\d{2})?\b",
    r"\b\d{1,2}(?:[:.]\d{2})? ?(?:h|hs|horas)\b",
]

# Día concreto (1 punto): "este sábado", "el próximo viernes", "15 de noviembre".
DIAS = [
    r"\beste (?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|fin de semana|finde)\b",
    r"\bel proximo (?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|fin de semana)\b",
    r"\b(?:hoy|manana|pasado manana) (?:a las|desde las|en)\b",
    r"\b\d{1,2} de (?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b",
]

# Frases que contienen palabras de agenda pero no lo son.
FALSOS_POSITIVOS = [
    r"\ben funcion de\b", r"\ben funciones\b", r"\bentrada en vigencia\b", r"\bentrada en vigor\b",
    r"\bmuestras? de (?:sangre|orina|agua|adn|laboratorio|solidaridad|apoyo)\b",
    r"\b(?:la|una) (?:encuesta|estadistica|cifra|el dato|el informe) muestra\b",
    r"\bobra publica\b", r"\bobras? de (?:infraestructura|pavimentacion|cloacas|gas|agua)\b",
    r"\bmesa de entradas\b", r"\bfiesta de la democracia\b",
]

_RE_FUERTES = re.compile(r"\b(?:" + "|".join(FUERTES) + r")\b")
_RE_DEBILES = re.compile(r"\b(?:" + "|".join(DEBILES) + r")\b")
_RE_HORAS = re.compile("|".join(HORAS))
_RE_DIAS = re.compile("|".join(DIAS))
_RE_FALSOS = re.compile("|".join(FALSOS_POSITIVOS))

UMBRAL = 3


def normalizar(texto: str) -> str:
    return _sin_tildes(texto.lower())


def senales(texto: str) -> dict:
    """Qué disparó el filtro y el puntaje (útil para depurar y para los tests)."""
    t = _RE_FALSOS.sub(" ", normalizar(texto))
    s = {
        "fuertes": sorted(set(_RE_FUERTES.findall(t))),
        "debiles": sorted(set(_RE_DEBILES.findall(t))),
        "horas": sorted({m.group(0) for m in _RE_HORAS.finditer(t)}),
        "dias": sorted({m.group(0) for m in _RE_DIAS.finditer(t)}),
    }
    s["puntaje"] = 2 * len(s["fuertes"]) + len(s["debiles"]) + 2 * bool(s["horas"]) + bool(s["dias"])
    return s


def pasa_prefiltro(texto: str) -> bool:
    """Pasa con puntaje >= 3 y al menos una palabra fuerte o una hora concreta.

    Ejemplos: "recital ... este sábado" = 2+1 (pasa). "dos jornadas de atención el 12 de
    noviembre" = 1+1 (no pasa). "encuentro de danzas el sábado a las 20" = 1+1+2+1 (pasa).
    """
    s = senales(texto)
    return s["puntaje"] >= UMBRAL and bool(s["fuertes"] or s["horas"])
