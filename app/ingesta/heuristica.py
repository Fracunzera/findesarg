"""Detección sin LLM: fechas, horas, lugar y categoría en el texto de una nota.

No decide nada por su cuenta: arma **sugerencias** para que quien modera cree el evento
con un clic desde /admin/notas. Por eso prefiere encontrar de más antes que de menos.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, time, timedelta

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
DIAS_SEMANA = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
_DIA = "(" + "|".join(DIAS_SEMANA) + ")"
_MES = "(" + "|".join(MESES) + ")"

# "sábado 15 de noviembre", "15 de noviembre de 2026", "1° de diciembre", "15 noviembre"
_RE_LARGA = re.compile(rf"(?:\b{_DIA},?\s+)?\b(\d{{1,2}})(?:°|º|ro)?\s+(?:de\s+)?{_MES}\b(?:\s+(?:de|del)\s+(20\d\d))?")
# "el sábado 15/11", "el 15/11/2026"
_RE_NUMERICA = re.compile(rf"(?:\b{_DIA}\s+|\bel\s+)(\d{{1,2}})/(\d{{1,2}})(?:/(20\d\d|\d\d))?\b")
# "el sábado 15" (sin mes)
_RE_DIA_NUM = re.compile(rf"\b{_DIA}\s+(\d{{1,2}})\b(?!\s*(?:°|º)?\s*(?:de\s+)?(?:{_MES[1:-1]})\b|\s*/|\s*(?:h|hs|horas)\b|[:.]\d)")
# "este sábado", "el próximo viernes", "el domingo"
_RE_RELATIVA = re.compile(rf"\b(este|esta|el proximo|el|proximo)\s+{_DIA}\b(?!\s+\d)(?!\s+pasado)")
_RE_HOY = re.compile(r"\b(hoy|manana)\b(?!\s+(?:de\s+)?(?:a la|por la|de la)\b)")
_RE_NO_HOY = re.compile(r"\b(?:la|por la|de la|esta|cada|toda la) manana\b")

# Horas: "a las 21", "desde las 18.30", "21 hs", "21:30", "a partir de las 20 horas"
_RE_HORA = re.compile(
    r"\b(?:a (?:partir )?de las|desde las|a las|de)\s+(\d{1,2})(?:[:.](\d{2}))?(?!\d|[.,/]\d)\s*(?:h|hs|horas)?\b"
    r"|(?<![\d.,$/])\b(\d{1,2})[:.](\d{2})(?!\d|[.,/]\d)\s*(?:h|hs|horas)?\b"
    r"|(?<![\d.,$/])\b(\d{1,2})\s*(?:h|hs|horas)\b"
)

# Frases de algo que ya pasó: si una oración las tiene, sus fechas no se sugieren.
_RE_PASADO = re.compile(
    r"\b(?:se realizo|se llevo a cabo|se desarrollo|culmino|finalizo|concluyo|tuvo lugar|"
    r"se vivio|reunio|participaron|asistieron|fue un exito|cerro con)\b"
)

_LUGARES = (
    "gimnasio|complejo|centro|teatro|club|salon|casa|museo|plaza|plazoleta|biblioteca|escuela|colegio|"
    "polideportivo|boxing|sociedad|hotel|cine|auditorio|anfiteatro|paseo|galpon|espacio|sede|parque|"
    "costanera|iglesia|catedral|instituto|universidad|unpa|estadio|predio|laguna|bar|pub|cafe|"
    "confiteria|camping|hipodromo|autodromo|vecinal|asociacion|circulo|templo|cooperativa"
)
_RE_LUGAR = re.compile(rf"\ben (?:el|la|los|las|nuestro|nuestra)?\s*((?:{_LUGARES})\b[^.,;:\n()\"“”]{{0,70}})")
_CORTES_LUGAR = re.compile(
    r"\s+(?:a (?:partir )?de las|a las|desde las|desde el|el (?:proximo|dia)|este|esta|ubicad[oa]|sit[oa]|"
    r"de (?:esta|nuestra) (?:ciudad|localidad|capital)|de rio gallegos|con entrada|donde|que|y se|para)\b.*$"
)

CATEGORIAS_CLAVE = {
    "musica": r"recital|concierto|banda|musica|musical|rock|folklore|folclor|pena|cantante|coro|orquesta|dj|cumbia|jazz|tango|milonga",
    "teatro": r"teatro|teatral|obra|stand ?up|comedia|actuacion|actriz|actor|elenco|monologo|clown|circo",
    "cine": r"cine|pelicula|proyeccion|documental|film|cortometraje|cortos",
    "feria": r"feria|mercado|emprendedor(?:es|as)?|artesan[oa]s|expo|bazar|venta",
    "deporte": r"torneo|maraton|carrera|partido|campeonato|boxeo|futbol|basquet|voley|handball|hockey|atletismo|ciclismo|natacion|copa",
    "taller": r"taller|curso|capacitacion|clase abierta|workshop|seminario|charla|conversatorio",
    "muestra": r"muestra|exposicion|exhibicion|galeria|museo|pinturas|fotografias|esculturas",
    "infantil": r"infantil(?:es)?|ninos|ninas|chicos|chicas|infancias|titeres|familia(?:s|r)?",
    "gastronomia": r"gastronomi[ac]o?|degustacion|food ?trucks?|cerveza|vinos?|cocina|asado|patio de comidas",
}
_RE_CATEGORIAS = {c: re.compile(rf"\b(?:{p})\b") for c, p in CATEGORIAS_CLAVE.items()}


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, con el mismo largo que el texto (para recortar del original)."""
    t = unicodedata.normalize("NFC", texto)
    salida = "".join(
        "".join(c2 for c2 in unicodedata.normalize("NFD", c) if unicodedata.category(c2) != "Mn") or c
        for c in t.lower()
    )
    return salida if len(salida) == len(t) else t.lower()


@dataclass
class FechaDetectada:
    dia: date
    hora: time | None
    fragmento: str  # la oración donde apareció (del texto original)


@dataclass
class Sugerencia:
    fechas: list[FechaDetectada] = field(default_factory=list)
    lugar: str | None = None
    categoria: str = "otro"


def _oraciones(texto: str) -> list[tuple[int, int]]:
    """Rangos (inicio, fin) de cada oración, para no mezclar la hora de una con la fecha de otra."""
    rangos, inicio = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¡¿\"“0-9])|\n+", texto):
        if m.start() > inicio:
            rangos.append((inicio, m.start()))
        inicio = m.end()
    if inicio < len(texto):
        rangos.append((inicio, len(texto)))
    return rangos


def _anio(dia: int, mes: int, ref: date) -> date | None:
    """Sin año: el de la nota, salvo que quede muy atrás (ej. "10 de enero" en diciembre)."""
    try:
        d = date(ref.year, mes, dia)
    except ValueError:
        return None
    if d < ref - timedelta(days=60):
        try:
            d = date(ref.year + 1, mes, dia)
        except ValueError:
            return None
    return d


def _proximo(dia_semana: int, ref: date, incluir_hoy: bool) -> date:
    delta = (dia_semana - ref.weekday()) % 7
    if delta == 0 and not incluir_hoy:
        delta = 7
    return ref + timedelta(days=delta)


def _hora(m: re.Match) -> time | None:
    if m.group(1) is not None:
        h, mi = m.group(1), m.group(2)
        # "de 18 a 22" tiene sentido; "de 2026" o "de 15 personas" no: sin unidad, solo de 8 a 23
        if m.group(0).startswith("de ") and not re.search(r"h|hs|horas|[:.]", m.group(0)[3:]) and not 8 <= int(h) <= 23:
            return None
    elif m.group(3) is not None:
        h, mi = m.group(3), m.group(4)
    else:
        h, mi = m.group(5), None
    h, mi = int(h), int(mi or 0)
    if h == 24:
        h = 0
    return time(h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def _fechas_en(oracion: str, ref: date) -> list[tuple[int, int, date]]:
    """(inicio, fin, día) de cada fecha mencionada en una oración ya normalizada."""
    hallazgos: list[tuple[int, int, date]] = []

    def agregar(m: re.Match, d: date | None):
        if d is None:
            return
        if any(not (m.end() <= a or m.start() >= b) for a, b, _ in hallazgos):
            return  # ya cubierto por una forma más completa
        hallazgos.append((m.start(), m.end(), d))

    for m in _RE_LARGA.finditer(oracion):
        dia, mes, anio = int(m.group(2)), MESES[m.group(3)], m.group(4)
        if anio:
            try:
                agregar(m, date(int(anio), mes, dia))
            except ValueError:
                pass
        else:
            agregar(m, _anio(dia, mes, ref))
    for m in _RE_NUMERICA.finditer(oracion):
        dia, mes, anio = int(m.group(2)), int(m.group(3)), m.group(4)
        if not (1 <= mes <= 12):
            continue
        if anio:
            try:
                agregar(m, date(int(anio) + (2000 if len(anio) == 2 else 0), mes, dia))
            except ValueError:
                pass
        else:
            agregar(m, _anio(dia, mes, ref))
    for m in _RE_DIA_NUM.finditer(oracion):
        dia_semana, dia = DIAS_SEMANA[m.group(1)], int(m.group(2))
        # El mes de la nota o el siguiente, el que coincida con el día de la semana
        for salto in (0, 1):
            mes = (ref.month - 1 + salto) % 12 + 1
            anio = ref.year + (1 if ref.month + salto > 12 else 0)
            try:
                d = date(anio, mes, dia)
            except ValueError:
                continue
            if d.weekday() == dia_semana and d >= ref - timedelta(days=1):
                agregar(m, d)
                break
    for m in _RE_RELATIVA.finditer(oracion):
        agregar(m, _proximo(DIAS_SEMANA[m.group(2)], ref, incluir_hoy=m.group(1) in ("este", "esta")))
    if not _RE_NO_HOY.search(oracion):
        for m in _RE_HOY.finditer(oracion):
            agregar(m, ref if m.group(1) == "hoy" else ref + timedelta(days=1))
    return sorted(hallazgos)


def detectar_fechas(texto: str, publicado: date) -> list[FechaDetectada]:
    """Fechas (con hora si está en la misma oración) a partir del día en que salió la nota."""
    original = unicodedata.normalize("NFC", texto)
    norm = normalizar(original)
    salida: dict[tuple[date, time | None], FechaDetectada] = {}
    for a, b in _oraciones(original):
        oracion = norm[a:b]
        if _RE_PASADO.search(oracion):
            continue
        fechas = _fechas_en(oracion, publicado)
        if not fechas:
            continue
        horas = [(m.start(), _hora(m)) for m in _RE_HORA.finditer(oracion)]
        horas = [(p, h) for p, h in horas if h is not None and not any(i <= p < f for i, f, _ in fechas)]
        fragmento = " ".join(original[a:b].split())
        for i, (ini, fin, dia) in enumerate(fechas):
            if not (publicado <= dia <= publicado + timedelta(days=365)):
                continue
            # La hora que viene después de esta fecha y antes de la siguiente; si hay una sola fecha, cualquiera
            hasta = fechas[i + 1][0] if i + 1 < len(fechas) else len(oracion)
            despues = [h for p, h in horas if fin <= p < hasta]
            hora = despues[0] if despues else (horas[0][1] if horas and len(fechas) == 1 else None)
            clave = (dia, hora)
            if clave not in salida:
                salida[clave] = FechaDetectada(dia, hora, fragmento[:400])
    # Si el mismo día aparece con y sin hora, queda el que tiene hora
    con_hora = {d for d, h in salida if h is not None}
    return sorted((f for (d, h), f in salida.items() if h is not None or d not in con_hora),
                  key=lambda f: (f.dia, f.hora or time(0, 0)))


def adivinar_lugar(texto: str) -> str | None:
    original = unicodedata.normalize("NFC", texto)
    norm = normalizar(original)
    for m in _RE_LUGAR.finditer(norm):
        recorte = _CORTES_LUGAR.sub("", m.group(1)).strip(" -–")
        texto_lugar = original[m.start(1): m.start(1) + len(recorte)].strip()
        # Nombre propio: "el Centro Polivalente" sí, "la casa había..." no
        if len(recorte) >= 4 and texto_lugar[:1].isupper():
            return texto_lugar
    return None


def adivinar_categoria(titulo: str, texto: str = "") -> str:
    """La categoría con más palabras clave; el título pesa el triple."""
    t_titulo, t_texto = normalizar(titulo), normalizar(texto)
    puntajes = {
        c: 3 * len(rx.findall(t_titulo)) + len(rx.findall(t_texto)) for c, rx in _RE_CATEGORIAS.items()
    }
    mejor = max(puntajes, key=lambda c: puntajes[c])
    return mejor if puntajes[mejor] >= 2 else "otro"


def sugerir(titulo: str, texto: str, publicado: date) -> Sugerencia:
    completo = f"{titulo}.\n{texto}" if titulo else texto
    fechas = detectar_fechas(completo, publicado)
    # El lugar se busca primero en las oraciones con fecha (suele estar ahí)
    lugar = next((l for f in fechas if (l := adivinar_lugar(f.fragmento))), None) or adivinar_lugar(completo)
    return Sugerencia(fechas=fechas, lugar=lugar, categoria=adivinar_categoria(titulo, texto))
