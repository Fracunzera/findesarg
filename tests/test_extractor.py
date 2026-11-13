import json
from types import SimpleNamespace

import pytest

from app.ingesta.extractor import (
    ESQUEMA_SALIDA, ErrorExtraccion, ExtraccionRechazada, Extractor, parsear_respuesta,
)
from app.ingesta.prompt_extraccion import PROMPT_EXTRACCION, armar_prompt


def test_prompt_se_arma_sin_romper_las_llaves():
    p = armar_prompt("Río Gallegos", "Santa Cruz", "America/Argentina/Rio_Gallegos", "2026-11-12")
    assert "ciudad de Río Gallegos, Santa Cruz, Argentina." in p
    assert "Fecha de publicación del texto: 2026-11-12." in p
    assert 'devolvé {"eventos": []}' in p
    assert "{ciudad}" not in p and "{fecha_publicacion}" not in p
    assert "distinta de Río Gallegos" in p


def test_prompt_tal_cual_del_enunciado():
    # Si alguien lo edita sin querer, salta acá.
    assert PROMPT_EXTRACCION.startswith("Sos un extractor de eventos para una agenda de la ciudad de {ciudad}")
    assert PROMPT_EXTRACCION.endswith('- Si no hay ningún evento, devolvé {"eventos": []}.')
    assert PROMPT_EXTRACCION.count("\n- ") == 7


def test_respuestas_simuladas_validan(esperado):
    for nombre, exp in esperado.items():
        r = parsear_respuesta(json.dumps(exp["respuesta_simulada"]))
        assert len(r.eventos) == len(exp["respuesta_simulada"]["eventos"]), nombre


def test_normaliza_mayusculas_categoria_y_confianza():
    r = parsear_respuesta(json.dumps({"eventos": [{
        "titulo": "GRAN PEÑA FOLKLÓRICA", "inicio": "2026-11-14T21:00:00-03:00", "ciudad": "Río Gallegos",
        "categoria": "folklore", "confianza": 1.7, "descripcion": "  ", "hora_confirmada": True,
    }]}))
    ev = r.eventos[0]
    assert ev.titulo == "Gran peña folklórica"
    assert ev.categoria == "otro"
    assert ev.confianza == 1.0
    assert ev.descripcion is None
    assert ev.inicio.utcoffset().total_seconds() == -3 * 3600


def test_json_invalido_es_error():
    with pytest.raises(ErrorExtraccion):
        parsear_respuesta("no es json")


def test_tolera_bloque_de_codigo():
    assert parsear_respuesta('```json\n{"eventos": []}\n```').eventos == []


class _ClienteFalso:
    def __init__(self, texto: str, stop_reason: str = "end_turn"):
        self.pedidos = []
        self._resp = SimpleNamespace(
            stop_reason=stop_reason, stop_details=SimpleNamespace(category="general_harms"),
            model="claude-haiku-5-5",
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=texto)],
            usage=SimpleNamespace(input_tokens=900, output_tokens=120),
        )
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.pedidos.append(kw)
        return self._resp


def test_pedido_al_modelo_y_lectura_por_tipo_de_bloque():
    cliente = _ClienteFalso('{"eventos": []}')
    r, crudo = Extractor(cliente=cliente, modelo="claude-haiku-5-5").extraer(
        "Recital el sábado", ciudad="Río Gallegos", provincia="Santa Cruz",
        timezone="America/Argentina/Rio_Gallegos", fecha_publicacion="2026-11-12",
    )
    assert r.eventos == [] and crudo["uso"] == {"entrada": 900, "salida": 120}
    pedido = cliente.pedidos[0]
    assert pedido["model"] == "claude-haiku-5-5"
    assert pedido["output_config"]["format"] == {"type": "json_schema", "schema": ESQUEMA_SALIDA}
    assert "temperature" not in pedido
    texto = pedido["messages"][0]["content"][-1]["text"]
    assert texto.startswith("Sos un extractor de eventos") and "Recital el sábado" in texto


def test_rechazo_del_modelo():
    with pytest.raises(ExtraccionRechazada):
        Extractor(cliente=_ClienteFalso("", stop_reason="refusal")).extraer(
            "x", ciudad="a", provincia="b", timezone="UTC", fecha_publicacion="2026-11-12")


def test_respuesta_cortada():
    with pytest.raises(ErrorExtraccion):
        Extractor(cliente=_ClienteFalso('{"eventos": [', stop_reason="max_tokens")).extraer(
            "x", ciudad="a", provincia="b", timezone="UTC", fecha_publicacion="2026-11-12")
