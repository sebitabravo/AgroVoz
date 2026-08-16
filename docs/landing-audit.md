# Auditoría de landing

**Última revisión local:** 16 de agosto de 2026<br>
**Deployment público documentado:** `dpl_76MbQk6fENe8PtL4wwpN3SgCTXev`<br>
**URL pública:** <https://landing-three-orpin-53.vercel.app><br>
**Estado:** los commits `043dc9a`, `35c1aa9`, `96b6129`, `fd0d038` y `cb7cab2`
fueron desplegados a producción en Vercel y aliasados al dominio público el 16
de agosto de 2026.
El push Git de la rama quedó pendiente por una restricción de aprobación de red
del entorno.

## Segunda revisión — 16/08/2026

Se revisó el commit `50e7861` y el diff posterior. Se corrigieron tres
inconsistencias detectadas:

- `DemoPhone` ahora encuentra el nombre institucional completo de Lo Valledor
  mediante coincidencia parcial, en vez de depender de una igualdad que la API
  real no devuelve.
- El plan ya no promete consultas ilimitadas; la demo mantiene el límite
  operativo de 5 solicitudes por minuto e IP.
- Las cifras de población objetivo e impacto quedaron etiquetadas como
  estimaciones/hipótesis pendientes de validación primaria. La clave EN quedó
  sincronizada con la cautela de ES.

Además, se citaron las seis escrituras de `GITHUB_OUTPUT` del workflow para que
`actionlint` no reporte `SC2086`.

## Alcance

La landing Astro tiene tres rutas públicas:

| Ruta | Propósito | Estado verificado |
|---|---|---|
| `/` | Presentación, propuesta de valor, stack, planes, impacto y contacto | `200`; layout responsive; el mockup está rotulado como vista simulada |
| `/demo/` | Chat tipo WhatsApp con texto, voz opcional y respuestas del backend | `200`; POST de precio real y render de respuesta en burbuja verificados |
| `/privacidad/` | Resumen técnico público y brechas antes del piloto | `200`; estado pendiente y fuentes visibles; no se presenta como política jurídica aprobada |

## Design system y UX

- Manrope se carga desde Google Fonts con fallback del sistema.
- La paleta usa los tokens de `global.css`; se eliminaron gradientes decorativos de la onda y del rediseño de privacidad.
- Header, botones, tarjetas, pills, tablas y footer mantienen el mismo lenguaje visual.
- Las animaciones de index usan `IntersectionObserver`, count-up y parallax acotado; `prefers-reduced-motion` desactiva los efectos.
- El drawer móvil usa `aria-expanded`, `aria-hidden` e `inert`.
- Se comprobó en Playwright que `/`, `/demo/` y `/privacidad/` no tienen overflow horizontal en el viewport de 390 px. Las tablas anchas de privacidad conservan scroll horizontal intencional y muestran una indicación al usuario.
- El selector ES/EN permanece solo en `/`: `/demo/` y `/privacidad/` no muestran un control que no tiene diccionario cargado.
- El formulario de contacto exige nombre, correo y mensaje antes de enviar al
  endpoint server-side `/api/contact`; el destinatario no aparece en el cliente.

## Uso real del backend

`/demo/` usa el proxy same-origin definido en `landing/vercel.json` y consulta:

- `POST /api/v1/demo/preguntar` para texto y `audio_base64` capturado por `MediaRecorder`.
- `GET /api/v1/prices/papa` para precios ODEPA por mercado y fecha.
- `GET /api/v1/weather` para clima actual de Traiguén.
- `GET /api/v1/data/sources` para estado, vigencia y cobertura del catálogo.

La portada conserva un teléfono ilustrativo para explicar el producto y enlaza a la demo operativa; no presenta ese mockup como una lectura live.

El formulario de contacto usa la Function server-side `api/contact.ts`. El
destinatario se guarda como `CONTACT_RECIPIENT` Sensitive en Vercel y nunca se
renderiza en la landing. La entrega requiere `RESEND_API_KEY`; mientras esa
credencial no esté configurada, el endpoint responde `503` de forma fail-closed.

## Evidencia ejecutada

```text
cd landing && bun run test
26 passed

PUBLIC_API_URL=https://agrovoz.sbravo.app bun run build
3 page(s) built

./test.sh
2262 backend passed, 3 skipped, coverage 86.31%
26 landing passed

cd backend && uv run ruff check app/ && uv run mypy app/
All checks passed; Success: no issues found in 105 source files

actionlint .github/workflows/ci.yml
sin errores

git diff --check
sin errores
```

Smoke público del deployment `dpl_76MbQk6fENe8PtL4wwpN3SgCTXev`:

```text
/ 200
/demo/ 200
/privacidad/ 200
/api/v1/health 200
/api/v1/prices/papa 200
/api/v1/weather 200
/api/v1/data/sources 200
POST /api/v1/demo/preguntar 200; intent=precio; audio_base64 presente
```

También se verificó en navegador que una consulta de tomate agrega una burbuja
enviada y una burbuja recibida con botón de audio, y que la tarjeta de precios
renderiza mercados y fechas provenientes de la API pública.

Verificación pública repetida después del nuevo deploy:

```text
/ 200
/demo/ 200; 6 chips
/privacidad/ 200
/api/v1/health 200; database=connected; ffmpeg=available
POST /api/v1/demo/preguntar 200; intent=precio; Lo Valledor; ODEPA
GET /api/v1/weather 200; Traiguén; OpenMeteo
Playwright: 6/6 chips con HTTP 200 y burbuja recibida
  - precio papa
  - lluvia Traiguén
  - precio tomate
  - calendario tomate
  - manchas en papa (regla INIA)
  - siembra trigo (regla INIA)
Sin `[object Object]`, sin `Reintentar` y sin burbujas de error
Headers: nosniff; strict-origin-when-cross-origin; camera=(), geolocation=(), microphone=(self)
```

La nueva burbuja `chat-bubble--typing` se agrega durante la espera de la
respuesta, contiene tres puntos animados y se elimina al recibir éxito o error;
el contrato está cubierto por `landing/tests/demo-page.test.ts`. El smoke público
del formulario responde `503` sin filtrar el destinatario porque todavía no hay
`RESEND_API_KEY` en Vercel Production; eso es el comportamiento fail-closed
esperado, no una entrega de correo exitosa.

Smoke HTTP directo del deployment `dpl_76MbQk6fENe8PtL4wwpN3SgCTXev`:

```text
/ 200 · /demo/ 200 · /privacidad/ 200
/api/v1/health 200 · /api/v1/prices/papa 200 · /api/v1/weather 200
/api/v1/data/sources 200 · POST /api/contact 503 (sin RESEND_API_KEY)
Dirección antigua y destinatario personal ausentes del HTML público.
Assets públicos contienen typing JS/CSS y formulario server-side.
```

La tercera verificación corrigió dos fallos encontrados en la prueba pública:

- El rewrite wildcard de clima no cubría correctamente `/api/v1/weather`; el
  patrón exacto ahora precede al wildcard y responde `200` desde Vercel.
- Las respuestas INIA largas excedían el límite de 500 caracteres por turno del
  historial y FastAPI devolvía `422`; el frontend recorta solo el contexto
  enviado, normaliza `detail` estructurado y valida `texto` antes de renderizar.

En la segunda revisión se repitió QA local con el servidor Astro y respuestas
mockeadas del backend: `DemoPhone` pasó a estado online, mostró el precio de la
respuesta institucional de Lo Valledor, el toggle ES/EN cambió estados y las
tres rutas no tuvieron overflow horizontal en un viewport de 390 px. Esto no
sustituye la prueba física de micrófono en Android Chrome.

## Brechas que siguen abiertas

- ~~La ruta de Vercel reescribe al backend completo (`agrovoz.sbravo.app`), no a la app slim descrita en `backend/app/vercel_demo.py`~~ — **resuelto 15/08/2026**: se documentó el estado realizado en `docs/ARCHITECTURE-DECISIONS.md` (ADR 34) y `docs/ARCHITECTURE.md` (filas 12 y 34): el rewrite al backend completo Docker es la topología vigente y el slim queda como diseño no desplegado.
- La captura física de micrófono requiere permiso HTTPS y no sustituye una prueba Android Chrome. La interfaz y el contrato `audio_base64` están implementados, pero la evidencia de dispositivo real sigue pendiente.
- Los claims de impacto, precios comerciales, costo mensual y margen son copy de producto/proyección; no deben tratarse como métricas productivas sin fuentes o aprobación separada.
- El contenido de `/privacidad/` sigue siendo un resumen técnico: responsable, canal de derechos, retenciones, backups y revisión formal de Ley 21.719 todavía requieren cierre humano/operativo.
- El catálogo público puede reportar fuentes `stale`; la landing lo muestra como estado pendiente y no lo convierte en dato vigente por defecto.
- `bun audit` queda limpio después de actualizar Astro a 7.2.0 y fijar overrides compatibles para `svgo`, `nanoid`, `postcss` y `sharp`; debe repetirse en cada actualización del lockfile.
- El CI no tiene un runner de navegador para permiso de micrófono; los tests de landing son contratos SSR/source y la evidencia física Android Chrome sigue pendiente.
- El formulario ya tiene endpoint server-side y destinatario `CONTACT_RECIPIENT`
  configurado como Sensitive en Vercel, pero falta `RESEND_API_KEY` para activar
  el envío real; sin esa credencial responde `503` de forma fail-closed.

## SPIKE de runtime — 15/08/2026

Consultas probadas contra `POST /api/v1/demo/preguntar` (deploy `agrovoz.sbravo.app`),
espaciadas por el rate limit de 5 consultas/minuto/IP. Pasa = respuesta distinta del
fallback "No tengo ese dato, pero puedo consultarte el precio en ODEPA o el clima".

| Consulta | Resultado | Estado |
|---|---|---|
| ¿A cuánto está la papa? | Precio ODEPA por mercado y fecha | ✅ chip aprobado |
| ¿Va a llover en Traiguén mañana? | OpenMeteo: máx 13°, mín 6°, 4,6 mm mañana / 2,7 mm pasado mañana | ✅ chip aprobado |
| ¿Cuál es el precio del tomate? | Precio ODEPA | ✅ chip aprobado |
| ¿Cuál es el calendario agrícola del tomate? | INIA Boletín N° 330: siembra 15/07–30/10, cosecha 15/01–30/04, con fuente y snapshot verificado 03/08/2026 | ✅ chip aprobado |
| ¿Qué hago si mis papas tienen manchas en las hojas? | INIA: tizón tardío (*Phytophthora infestans*) citado con condiciones, síntomas y fuente | ✅ chip aprobado |
| ¿Cuándo se siembra el trigo en Traiguén? | INIA: trigo de invierno, siembra 15/04–30/05, cosecha 15/01–28/02, fuente citada | ✅ chip aprobado |
| ¿Cuánto cuesta la semilla de papa? | "No tengo datos verificables del precio de semillas. ODEPA informa precios de productos frescos, no de semillas." | ⚠️ fail-closed honesto; no se expone como chip (demo negativa) |
| ¿Cómo estuvo el clima en Traiguén la semana pasada? | Devuelve clima actual, no histórico | ⚠️ no se expone como chip |
| ¿Qué regla agronómica hay para el tomate? / ¿Qué programas INDAP existen? / ¿Dónde queda la oficina INDAP en Temuco? / ¿Qué sabe hacer AgroVoz? | Fallback "No tengo ese dato..." | ❌ fuera de alcance: el demo cortocircuita `consulta_tipo=="desconocido"` en `demo_service.py` sin resolver capacidades/directorio/programas; arreglarlo exige backend + redeploy |

**Conclusión del SPIKE:** la demo expone 6 chips verificados (2 precio, 1 clima, 3 INIA
citado). Las capacidades de INDAP/directorio/catálogo quedan acotadas en el copy de la
landing como alcance del canal WhatsApp en piloto, no como función probable en la demo.
