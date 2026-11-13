# Fuentes evaluadas

Antes de usar una fuente se revisa su `robots.txt`, sus términos y si tiene RSS o API. Si no se puede usar, queda anotada acá con el motivo. Última revisión: **2026-11-12**.

**Cómo usamos el contenido.** De cada nota guardamos el texto en `item_crudo` (uso interno: auditar y no reprocesar). En la web pública mostramos solo los datos del evento (qué, cuándo, dónde, precio) y una descripción de 1 a 3 oraciones escrita por el LLM "con sus palabras", siempre con el enlace a la nota original ("Fuente: ..."). No republicamos notas.

**Cómo pedimos.** User-Agent `quehacer-bot/0.1 (+SITIO_URL)`, un pedido cada 5 s por dominio como mínimo (más si el `Crawl-delay` lo pide), reintentos con backoff, caché en disco y nada de redes sociales.

## En uso

| Fuente | URL | Tipo | robots.txt / términos | Por qué |
|---|---|---|---|---|
| Municipalidad de Río Gallegos: calendario | https://riogallegos.gob.ar/api/calendario-activities | `api` (JSON) | `User-agent: * / Disallow:` (todo permitido). Es el JSON que usa la propia web del municipio para su calendario. No encontramos términos de uso | Agenda oficial y ya estructurada (título, inicio, fin, lugar, "hora a confirmar"). **No pasa por el LLM.** Hoy tiene 44 actividades de jul–oct 2026; el municipio lo carga de a ratos |
| Municipalidad de Río Gallegos: noticias | https://riogallegos.gob.ar/api/news/latest | `api` (JSON) | Igual que la anterior. Cada nota está en `https://riogallegos.gob.ar/noticias/{slug}` (verificado, 200) | Gacetillas de Cultura, Juventud y otras áreas, con el texto completo. Trae solo las 3 últimas: se consulta cada 3 h |
| Prensa del Gobierno de Santa Cruz | https://noticias.santacruz.gob.ar/?format=feed&type=rss | `rss` | Joomla estándar: solo bloquea carpetas internas (`/administrator/`, `/api/`, etc.). El RSS lo publica el sitio en su `<link rel="alternate">` | Gacetillas provinciales, entre ellas las de Cultura y Turismo. 10 ítems por feed |
| La Opinión Austral | https://laopinionaustral.com.ar/sitemaps/sitemaps-googlenews.xml | `html` (índice: sitemap de noticias) | `User-agent: *` sin `Disallow`, y publica sus sitemaps en el robots. Sin términos de uso en el sitio, solo [política de privacidad](https://laopinionaustral.com.ar/politica-de-privacidad.html). **No tiene RSS** (`/feed/`, `/rss`, `/rss.xml` y `/feed.xml` dan 404) | Diario principal de Río Gallegos. El sitemap de Google News trae URL, título y fecha (unas 60 notas). Se excluyen por URL las secciones que nunca traen agenda local (Argentina, El Mundo, Policiales, Política, otras ciudades) |
| TiempoSur | https://www.tiemposur.com.ar/cultura y /espectaculos | `html` (índice: listados) | `Allow: /` con **`Crawl-delay: 10`** (lo respetamos). Pie: "Todos los derechos reservados" (no republicamos textos). Sin RSS: `/feed/` devuelve la portada. Su sitemap de Google News da 404 | Diario de Río Gallegos con secciones de Cultura y Espectáculos |
| Santa Cruz al Momento | https://www.santacruzalmomento.com.ar/ y /categoria/cultura-2, /categoria/espectaculos | `html` (índice: listados) | `Allow: /`, `Disallow: /redaccion`. Sin términos publicados. Sin RSS (`/feed/` da 404) | Portal de noticias de la provincia con mucha agenda de Río Gallegos. Las URLs traen la fecha (`/2026/11/07/...`), así se descartan las viejas sin bajarlas |
| OpenStreetMap vía Overpass | https://overpass-api.de/api/interpreter (y espejos) | API | [Política de uso](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html): uso moderado, UA identificable. ODbL: hay que atribuir. Rechaza UAs con `example.com` (406) | Plazas y parques (fase 1). Una consulta por semana |
| Tiles de OpenStreetMap | https://tile.openstreetmap.org | Tiles | [Tile usage policy](https://operations.osmfoundation.org/policies/tiles/): atribución visible y tráfico moderado | Los carga el navegador del visitante |

## Evaluadas y no usadas (por ahora)

| Fuente | URL probada | Resultado | Qué falta |
|---|---|---|---|
| Nuevo Día | nuevodia.com.ar, diarionuevodia.com.ar, elnuevodia.com.ar | Ninguno resuelve (sin respuesta) | **TODO: verificar** el dominio real del diario |
| Secretaría de Cultura de Santa Cruz | https://noticias.santacruz.gob.ar/organismos/cultura?format=feed&type=rss | 404 | No hay feed propio. Sus gacetillas entran por el RSS general de Prensa Santa Cruz |
| Turismo de Santa Cruz | santacruzpatagonia.gob.ar | Sin respuesta | **TODO: verificar** el sitio oficial actual |
| La Vanguardia Noticias, OPI Santa Cruz | lavanguardianoticias.com.ar, opisantacruz.com.ar | Responden (200), sin revisar robots ni términos todavía | Candidatas para una próxima tanda |

## Prohibidas por diseño

Instagram, Facebook, TikTok, X y Google Maps/Places: **no se scrapean**. Instagram entra solo por la Graph API oficial, con cuentas que lo autorizaron (fase 4).
