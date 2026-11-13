"""Configuración leída del entorno (.env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://quehacer:quehacer@localhost:5432/quehacer"
    sitio_nombre: str = "Qué hacer"  # nombre provisorio: se cambia en .env
    sitio_url: str = "https://github.com/Fracunzera/findesarg"
    overpass_urls: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter"
    )
    segundos_entre_requests: float = 5.0

    anthropic_api_key: str = ""
    llm_modelo: str = "claude-haiku-5-5"
    # Directorio de caché del HTML bajado (volumen del worker)
    dir_cache: str = "/srv/data/cache"
    # No procesar notas publicadas hace más de N días
    dias_max_nota: int = 14
    # Tope de notas nuevas por fuente y por corrida (acota requests y gasto de LLM)
    max_items_por_corrida: int = 25
    # Panel /admin. Sin ADMIN_CLAVE el panel queda deshabilitado.
    admin_usuario: str = "admin"
    admin_clave: str = ""
    # Firma las cookies de sesión y los formularios. Si falta, se genera una al arrancar
    # (las sesiones se pierden con cada reinicio).
    secreto: str = ""
    # Tope de eventos que se pueden cargar desde el formulario por IP y por hora
    max_envios_por_hora: int = 5

    @property
    def user_agent(self) -> str:
        # Nos identificamos siempre, como pide la política de uso de cada fuente.
        return f"quehacer-bot/0.1 (+{self.sitio_url})"

    @property
    def lista_overpass(self) -> list[str]:
        return [u.strip() for u in self.overpass_urls.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
