import time

import httpx

from app.http import ClienteEducado


def _cliente(handler, **kw) -> ClienteEducado:
    c = ClienteEducado(**kw)
    ua = c.http.headers["User-Agent"]
    c.http = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": ua})
    return c


def test_se_identifica_con_user_agent_propio():
    vistos = []

    def handler(req):
        vistos.append(req.headers["User-Agent"])
        return httpx.Response(200, text="ok")

    with _cliente(handler, segundos_entre_requests=0) as c:
        c.get("https://ejemplo.test/")
    assert vistos[0].startswith("quehacer-bot/0.1 (+")


def test_respeta_el_intervalo_por_dominio():
    def handler(req):
        return httpx.Response(200)

    with _cliente(handler, segundos_entre_requests=0.3) as c:
        t0 = time.monotonic()
        c.get("https://a.test/1")
        c.get("https://b.test/1")  # otro dominio: no espera
        t1 = time.monotonic()
        c.get("https://a.test/2")  # mismo dominio: espera
        t2 = time.monotonic()
    assert t1 - t0 < 0.25
    assert t2 - t0 >= 0.28


def test_reintenta_ante_502_y_despues_anda():
    respuestas = iter([httpx.Response(502), httpx.Response(200, text="listo")])

    with _cliente(lambda req: next(respuestas), segundos_entre_requests=0, backoff_inicial=0.01) as c:
        assert c.get("https://a.test/").text == "listo"
