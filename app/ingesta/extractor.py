"""Extracción de eventos con Claude (modelo barato: claude-haiku-5-5).

Usa el prompt de prompt_extraccion.py tal cual y, además, pide salida estructurada
(output_config.format) para que la respuesta sea siempre JSON válido. Después se
valida todo con Pydantic: nada de lo que devuelve el modelo entra sin validar.
"""
import json
import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import get_settings
from app.ingesta.prompt_extraccion import armar_prompt
from app.models import CATEGORIAS

log = logging.getLogger(__name__)

Categoria = Literal["musica", "teatro", "cine", "feria", "deporte", "taller", "muestra", "infantil", "gastronomia", "otro"]


class EventoExtraido(BaseModel):
    titulo: str = Field(min_length=2, max_length=300)
    descripcion: str | None = None
    inicio: datetime | None = None
    fin: datetime | None = None
    hora_confirmada: bool = False
    lugar_nombre: str | None = None
    direccion: str | None = None
    ciudad: str = ""
    precio_texto: str | None = None
    gratis: bool | None = None
    categoria: Categoria = "otro"
    confianza: float = 0.0

    @field_validator("titulo")
    @classmethod
    def sin_mayusculas_sostenidas(cls, v: str) -> str:
        v = " ".join(v.split())
        letras = [c for c in v if c.isalpha()]
        if letras and sum(c.isupper() for c in letras) / len(letras) > 0.7:
            v = v.capitalize()
        return v

    @field_validator("descripcion", "lugar_nombre", "direccion", "precio_texto", mode="before")
    @classmethod
    def vacio_es_null(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("categoria", mode="before")
    @classmethod
    def categoria_conocida(cls, v):
        v = (v or "otro").strip().lower()
        return v if v in CATEGORIAS else "otro"

    @field_validator("confianza", mode="before")
    @classmethod
    def confianza_en_rango(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


class RespuestaExtraccion(BaseModel):
    eventos: list[EventoExtraido] = []


class ErrorExtraccion(Exception):
    """La extracción falló de una forma que conviene reintentar más tarde."""


class ExtraccionRechazada(Exception):
    """El modelo se negó a procesar el texto (stop_reason == "refusal"). No se reintenta."""


# Esquema para output_config.format. Sin minimum/maximum: el rango lo controla Pydantic.
_NULO_O = lambda t: {"type": [t, "null"]}  # noqa: E731
ESQUEMA_SALIDA = {
    "type": "object",
    "properties": {
        "eventos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "descripcion": _NULO_O("string"),
                    "inicio": _NULO_O("string"),
                    "fin": _NULO_O("string"),
                    "hora_confirmada": {"type": "boolean"},
                    "lugar_nombre": _NULO_O("string"),
                    "direccion": _NULO_O("string"),
                    "ciudad": {"type": "string"},
                    "precio_texto": _NULO_O("string"),
                    "gratis": _NULO_O("boolean"),
                    "categoria": {"type": "string", "enum": list(CATEGORIAS)},
                    "confianza": {"type": "number"},
                },
                "required": [
                    "titulo", "descripcion", "inicio", "fin", "hora_confirmada", "lugar_nombre",
                    "direccion", "ciudad", "precio_texto", "gratis", "categoria", "confianza",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["eventos"],
    "additionalProperties": False,
}

# Tope de caracteres del texto: una nota normal anda por 3-8 mil. Más que esto suele ser
# basura de la página (comentarios, notas relacionadas), y se avisa en el log.
MAX_CARACTERES = 30_000


def parsear_respuesta(texto_json: str) -> RespuestaExtraccion:
    """Valida el JSON del modelo. Tolera un bloque ```json por si acaso."""
    t = texto_json.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    try:
        return RespuestaExtraccion.model_validate(json.loads(t))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ErrorExtraccion(f"Salida inválida del modelo: {e}") from e


class Extractor:
    def __init__(self, cliente=None, modelo: str | None = None):
        s = get_settings()
        self.modelo = modelo or s.llm_modelo
        if cliente is None:
            import anthropic

            if not s.anthropic_api_key:
                raise ErrorExtraccion("Falta ANTHROPIC_API_KEY en .env")
            cliente = anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=3, timeout=120)
        self.cliente = cliente

    def extraer(
        self,
        texto: str,
        *,
        ciudad: str,
        provincia: str,
        timezone: str,
        fecha_publicacion: str,
        imagenes: list[dict] | None = None,
    ) -> tuple[RespuestaExtraccion, dict]:
        """Devuelve (respuesta validada, datos crudos para auditar).

        `imagenes` (fase 3, flyers): lista de bloques {"type": "image", "source": {...}}.
        """
        import anthropic

        if len(texto) > MAX_CARACTERES:
            log.warning("Texto de %d caracteres; se recorta a %d", len(texto), MAX_CARACTERES)
            texto = texto[:MAX_CARACTERES]

        prompt = armar_prompt(ciudad, provincia, timezone, fecha_publicacion)
        contenido: list[dict] = list(imagenes or [])
        contenido.append({"type": "text", "text": f"{prompt}\n\nTexto:\n\"\"\"\n{texto}\n\"\"\""})

        try:
            resp = self.cliente.messages.create(
                model=self.modelo,
                max_tokens=16000,
                # Extracción simple: poco razonamiento alcanza y sale más barato.
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ESQUEMA_SALIDA},
                },
                messages=[{"role": "user", "content": contenido}],
            )
        except anthropic.BadRequestError as e:
            raise ExtraccionRechazada(f"Pedido inválido: {e.message}") from e
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            raise ErrorExtraccion(f"Error de la API de Anthropic: {e}") from e

        if resp.stop_reason == "refusal":
            categoria = getattr(getattr(resp, "stop_details", None), "category", None)
            raise ExtraccionRechazada(f"El modelo rechazó el texto (categoría: {categoria})")
        if resp.stop_reason == "max_tokens":
            raise ErrorExtraccion("La respuesta se cortó por max_tokens")

        texto_json = next((b.text for b in resp.content if b.type == "text"), "")
        crudo = {
            "modelo": resp.model,
            "respuesta": texto_json,
            "uso": {"entrada": resp.usage.input_tokens, "salida": resp.usage.output_tokens},
        }
        return parsear_respuesta(texto_json), crudo
