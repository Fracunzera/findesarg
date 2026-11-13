"""Bot de Telegram para organizadores (Fase 3).

Por ahora solo queda en espera para que el servicio exista en el compose.
"""
import logging
import signal
import threading

log = logging.getLogger("bot")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("El bot de Telegram llega en la fase 3; me quedo esperando.")
    fin = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: fin.set())
    fin.wait()


if __name__ == "__main__":
    main()
