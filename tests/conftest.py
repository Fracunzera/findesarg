import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
NOTAS = FIXTURES / "notas"


@pytest.fixture(scope="session")
def esperado() -> dict:
    datos = json.loads((NOTAS / "esperado.json").read_text(encoding="utf-8"))
    datos.pop("_comentario", None)
    return datos


def leer_nota(nombre: str) -> str:
    return (NOTAS / nombre).read_text(encoding="utf-8")
