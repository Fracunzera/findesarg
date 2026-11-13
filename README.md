# quehacer

Agenda de **qué hacer en la ciudad** (por ahora, Río Gallegos, Santa Cruz): eventos de la semana y del finde, y un mapa de plazas y espacios verdes. Los datos salen de fuentes abiertas y de organizadores que los mandan. **No se scrapean redes sociales.**

Estado: **Fase 2** (ingesta de fuentes abiertas) terminada. Fase 1: base y lugares.

## Stack

- Python 3.12 + FastAPI, Jinja2 + HTMX (renderizado en el servidor)
- PostgreSQL 16 + PostGIS, migraciones con Alembic
- Leaflet + tiles de OpenStreetMap (Leaflet y HTMX vienen incluidos en `app/static/vendor/`, sin CDN)
- APScheduler dentro del contenedor `worker` (sin colas externas)
- Ingesta: `httpx`, `feedparser`, `trafilatura`, `rapidfuzz` y la API de Anthropic (`claude-haiku-5-5`) para extraer eventos

Servicios del compose: `db`, `web`, `worker` y `bot` (el bot por ahora es un placeholder, llega en la fase 3).

## Levantarlo

```bash
cp .env.example .env
# Editá .env: como mínimo POSTGRES_PASSWORD y SITIO_URL
docker compose up -d --build
```

- `web` aplica las migraciones (`alembic upgrade head`) antes de arrancar.
- `worker` carga las ciudades de `config/ciudades.yaml` y, si una ciudad no tiene lugares, los importa de OSM. Después los actualiza todos los lunes a las 4:17.
- `worker` también corre la **ingesta** cada 30 minutos: cada fuente se procesa cuando le toca según su `cada_horas`.
- Sin `ANTHROPIC_API_KEY`, la ingesta igual baja y prefiltra las notas; las que pasan el prefiltro quedan esperando y se extraen apenas cargues la key.
- La web queda en `http://WEB_BIND:WEB_PORT` (por defecto `127.0.0.1:8000`). En el VPS, poné un reverse proxy (Nginx, Caddy, NPM) adelante.

> **`SITIO_URL` tiene que ser una URL real.** Va en el User-Agent (`quehacer-bot/0.1 (+SITIO_URL)`), y `overpass-api.de` rechaza `example.com` con un 406.

### Comandos útiles

```bash
# Importar (o reimportar) las plazas a mano
docker compose exec worker python -m app.cli importar-lugares rio-gallegos

# Volver a sincronizar config/ciudades.yaml con la base
docker compose exec worker python -m app.cli sincronizar-ciudades

# Correr la ingesta ya (todas las fuentes, o una sola)
docker compose exec worker python -m app.cli ingestar --forzar
docker compose exec worker python -m app.cli ingestar --fuente tiemposur

# Probar el prefiltro y el LLM con un texto cualquiera (no guarda nada)
docker compose exec worker python -m app.cli probar-extraccion tests/fixtures/notas/02_agenda_finde.txt --fecha 2026-11-12

# Tests (usan la base del compose; los que necesitan DB se saltean si no hay)
docker compose run --rm web pytest
# Incluye 7 tests contra la API real de Claude, que solo corren si hay ANTHROPIC_API_KEY

# Logs
docker compose logs -f worker
```

## Páginas

| Ruta | Qué muestra |
|---|---|
| `/` | Redirige a la primera ciudad |
| `/rio-gallegos` | Portada (la agenda llega en las fases 2 y 5) |
| `/rio-gallegos/plazas` | Mapa y lista de plazas, parques, juegos y jardines. Filtro por tipo, búsqueda y orden por cercanía |
| `/estado/fuentes` | Panel de salud de las fuentes: última corrida, ítems, errores y últimos eventos detectados (público por ahora; pasa a `/admin` en la fase 3) |
| `/salud` | Healthcheck (JSON) |

Sin JavaScript, la página de plazas sigue andando: el formulario hace GET y la lista se renderiza entera.

## Cómo agregar una ciudad

1. Agregá una entrada en `config/ciudades.yaml`:
   ```yaml
   - slug: caleta-olivia
     nombre: Caleta Olivia
     provincia: Santa Cruz
     timezone: America/Argentina/Rio_Gallegos
     bbox: [sur, oeste, norte, este]
   ```
   El bbox se puede sacar de <https://boundingbox.klokantech.com/> (formato CSV, ojo con el orden: acá va **sur, oeste, norte, este**).
2. `docker compose restart worker` (sincroniza la ciudad e importa sus lugares si no tiene), o corré los dos comandos de la sección anterior.

## Cómo agregar una fuente

1. **Evaluala primero.** Revisá su `robots.txt` y sus términos, buscá si tiene RSS (`<link rel="alternate" type="application/rss+xml">` en el HTML) o un sitemap de Google News (suele estar listado en el `robots.txt`). Anotá el resultado en [`docs/fuentes.md`](docs/fuentes.md), se use o no. **No inventes URLs**: si no pudiste confirmarla, dejala comentada con `TODO: verificar`.
2. **Agregala a `config/fuentes.yaml`**, en orden de preferencia:
   ```yaml
   # RSS (lo mejor)
   - clave: mi-diario
     ciudad: rio-gallegos
     nombre: Mi Diario
     tipo: rss
     url: https://midiario.com.ar/feed/
     cada_horas: 3

   # Sin RSS pero con sitemap de Google News
   - clave: otro-diario
     ciudad: rio-gallegos
     nombre: Otro Diario
     tipo: html
     url: https://otrodiario.com.ar/
     indice:
       tipo: sitemap
       urls: [https://otrodiario.com.ar/sitemap-news.xml]
       patron: '^https://otrodiario\.com\.ar/(cultura|sociedad)/'   # opcional
     cada_horas: 3

   # Solo HTML: páginas de listado + regex de las URLs de notas
   - clave: portal
     ciudad: rio-gallegos
     nombre: Portal
     tipo: html
     url: https://portal.com.ar/
     indice:
       tipo: enlaces
       urls: [https://portal.com.ar/cultura]
       patron: '^https://portal\.com\.ar/cultura/[a-z0-9-]+$'
     cada_horas: 4
   ```
   Una fuente con JSON propio (`tipo: api`) necesita un lector nuevo en `app/ingesta/lectores.py`.
3. El worker relee el YAML en cada vuelta. Para probarla ya: `docker compose exec worker python -m app.cli ingestar --fuente mi-diario` y mirá `/estado/fuentes`.
4. Si pusiste un `patron`, agregá un caso a `tests/test_lectores.py`.

Para pausar una fuente sin borrarla: `activa: false`. Si sacás una fuente del YAML, queda inactiva en la base (no se borran sus datos).

## Ingesta (fase 2)

```
índice (RSS / sitemap / listado / API)
  └─ notas nuevas: no vistas, de los últimos 14 días, hasta 25 por fuente y corrida
      └─ bajar la nota (respeta robots.txt + caché) → texto limpio con trafilatura
          └─ item_crudo (se guarda siempre: auditoría y no reprocesar)
              └─ prefiltro de palabras de agenda ──✗──> descartada (no se gasta LLM)
                  └─ claude-haiku-5-5 con el prompt de app/ingesta/prompt_extraccion.py → Pydantic
                      └─ por evento: filtro de ciudad → dedup (±1 día, rapidfuzz) → publicado | pendiente
```

- **Publicado** si la confianza es ≥ 0,8 y el evento todavía no pasó. Si no, **pendiente** (se modera en la fase 3).
- **Sin hora segura** el evento se guarda a las 00:00 con `hora_confirmada = false`, y la web dice "Horario a confirmar". Nunca se inventa una hora.
- **Deduplicación:** se normalizan título y lugar (minúsculas, sin tildes ni palabras vacías) y se comparan con `token_set_ratio` contra los eventos de ±1 día. Si es el mismo evento se queda el registro más completo y la otra URL va a `urls_alternativas`.
- **Calendario municipal:** viene estructurado, así que entra directo, sin LLM, con confianza 0,9.
- **Errores:** una fuente rota no frena a las demás. El error queda en el panel. Si el LLM falla, la nota se reintenta en la próxima corrida (hasta 3 veces). Si el modelo la rechaza, no se reintenta.
- **Costo:** con Haiku 5.5 a USD 0,10 / 0,50 por millón de tokens, una nota típica (unos 2.000 tokens de entrada) cuesta alrededor de USD 0,0003. Por el prefiltro, solo una parte de las notas llega al LLM.

## Lugares (OSM)

- Se consultan `leisure=park|garden|playground` y `place=square` dentro del bbox de la ciudad, con `out center` (un punto por elemento).
- Se descartan los elementos con `access=private|no|customers` y los `leisure=garden` **sin nombre**: en Río Gallegos son 44 de 45 y casi todos son jardines privados o canteros.
- Tipos: `place=square` o nombre que arranca con "Plaza"/"Plazoleta" → **plaza**; `park` → **parque**; `playground` → **juegos**; `garden` → **jardín**.
- `opening_hours` se guarda crudo en `horario_osm`. Si no existe, se muestra "Sin horario informado (generalmente abierta)". Nunca se inventa un horario.
- Si un elemento desaparece de OSM, se borra en la próxima importación, salvo que algún evento lo use.
- Requests: User-Agent identificado, un request cada `SEGUNDOS_ENTRE_REQUESTS` por dominio, reintentos con backoff exponencial ante 429/502/503/504, y varios espejos de Overpass que se prueban en orden.

## Accesibilidad

- Los tipos de lugar siempre llevan **ícono + texto**. El color de los marcadores es solo un refuerzo.
- La lista es la fuente principal; el mapa la complementa (cada ítem tiene "Ver en el mapa" y cada marcador tiene "Ver en la lista").
- Contraste AA o mejor, foco visible, link para saltar al contenido, objetivos táctiles de 44 px como mínimo, y la ubicación anunciada con `aria-live`.

## Estructura

```
app/
  main.py            rutas web
  models.py          modelo de datos (ciudad, lugar, fuente, item_crudo, evento)
  http.py            cliente HTTP con rate limit, UA y backoff
  ciudades.py        config/ciudades.yaml → base
  lugares/           Overpass: consulta, parseo e importación
  ingesta/           fuentes.yaml → lectores → prefiltro → extractor (LLM) → dedup → pipeline
  worker.py          APScheduler
  cli.py             comandos de mantenimiento
  templates/, static/
alembic/             migraciones
config/              ciudades.yaml y fuentes.yaml
docs/                fuentes.md
tests/               pytest + fixtures
```

## Licencias de datos

Datos de lugares © colaboradores de OpenStreetMap, licencia ODbL. Tiles © OpenStreetMap, usados según su [política de tiles](https://operations.osmfoundation.org/policies/tiles/). Si el tráfico crece, conviene pasar a un proveedor de tiles propio o pago.
