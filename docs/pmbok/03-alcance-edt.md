# Enunciado de alcance y EDT de AgroVoz

## Propósito

Este documento establece qué producto se entrega, qué trabajo forma parte de
AgroVoz y cómo se acepta. La EDT representa el producto implementado y el
trabajo de validación pendiente; no reproduce el plan temprano de la
[Discussion #137][d137].

## Enunciado del alcance

AgroVoz entrega a pequeños agricultores chilenos un asistente de información
por WhatsApp. El usuario puede enviar audio o texto para consultar precios
agrícolas oficiales, clima y cálculos relacionados. El backend procesa la
consulta con componentes locales, usa herramientas limitadas para acceder a
datos estructurados y responde por el mismo canal.

El producto incluye operación, administración y preparación de un piloto
acotado. No improvisa recomendaciones agronómicas: solo verbaliza reglas
determinísticas con fuente INIA/INDAP citada. Precio y clima se entregan como
datos crudos, sin interpretación.

## Requisitos de alto nivel

- WhatsApp es la interfaz principal y no requiere instalar otra aplicación.
- Voz y texto son vías de entrada de primera clase.
- Whisper, LLM, TTS y gateway se ejecutan con stack open-source.
- Los precios provienen de ODEPA y el clima de OpenMeteo.
- El LLM solo puede invocar herramientas incluidas en una whitelist.
- El sistema funciona con SQLite y procesamiento síncrono.
- Las respuestas deben mantenerse bajo 15 segundos en el piso soportado de 1
  vCPU y 4 GB RAM; el benchmark que lo demuestre sigue pendiente.
- La identidad del agricultor es su número de WhatsApp.
- El sistema informa datos; no prescribe acciones.
- La privacidad se controla con minimización/seudonimización, opt-in y retención limitada.

## Alcance incluido

### Producto

- Recepción de mensajes de audio y texto.
- Conversión de audio, transcripción, clasificación, herramientas, composición
  de respuesta y síntesis de voz.
- Consulta de precios actuales, históricos y spread entre mercados.
- Cálculo determinístico de valor de venta y margen.
- Clima actual e histórico por comuna registrada.
- Consulta de corpus oficial y registro de gastos por voz.
- Alertas de precio, helada y lluvia extrema.
- Preferencias, conversación con estado e historial consentido.
- Catálogo ODEPA declarado de 79 productos y 15 mercados; el conteo sobre un
  snapshot reproducible queda como gate pendiente.

### Operación y administración

- Webhook FastAPI y gateway Open-WA.
- Sincronización diaria de ODEPA, cache local y degradación controlada.
- Landing estática, demo web y dashboard administrativo de ocho vistas.
- Autenticación de administración mediante sesión firmada o `X-Admin-Key`.
- Métricas, revisión de consultas, monitor y controles del piloto.
- Configuración de Docker Compose de desarrollo y producción, más
  documentación de VPS/Dokploy; no se afirma un despliegue efectivo.

### Validación

- Pruebas automatizadas de servicios, APIs y regresión.
- Ruff y mypy en modo estricto.
- Smoke CI de tests, Ruff y mypy, separado del benchmark de latencia.
- Benchmark de latencia en el piso de 1 vCPU y 4 GB RAM, y evaluación WER; ambos
  permanecen pendientes de evidencia específica.
- Prueba E2E con WhatsApp autenticado.
- Piloto planificado de cuatro semanas con 3–5 productores.
- Auditoría formal de privacidad antes de escalar.

## Fuera del alcance

- Diagnóstico o recomendaciones agronómicas improvisadas sin regla ni fuente citada (se permite la identificación determinística de plagas/enfermedades y calendarios citando fuentes INIA/INDAP).
- App nativa iOS/Android (WhatsApp es la vía principal; PWA es el complemento opcional).
- Sensores IoT o hardware adicional (solo micrófono y cámara del teléfono).
- Pagos integrados y planes de suscripción para agricultores.
- Operación multi-idioma.
- APIs pagas como dependencia del flujo principal.
- PostgreSQL, Turso, Redis, Celery u otro servidor de persistencia/colas.
- Cobertura garantizada fuera del catálogo ODEPA.
- Canal IVR como parte de la línea base actual; [#172][i172] sigue abierto.

## EDT

```text
1. Dirección y alcance
   1.1 Acta de constitución
   1.2 Plan para la dirección
   1.3 Enunciado de alcance y EDT
   1.4 Matriz de trazabilidad

2. Backend de información
   2.1 API FastAPI y persistencia SQLite
   2.2 Catálogo y sincronización ODEPA
   2.3 Cliente y cache OpenMeteo
   2.4 Servicios determinísticos y Tool Calling
   2.5 Preferencias, historial y estado conversacional
   2.6 Alertas y trabajos programados
   2.7 Privacidad, seguridad y observabilidad

3. Canal conversacional
   3.1 Webhook y normalización Open-WA
   3.2 Entrada de texto
   3.3 Audio, ffmpeg y Whisper
   3.4 LLM local y fallbacks
   3.5 Piper TTS y entrega de audio

4. Interfaces web
   4.1 Landing Astro
   4.2 Demo interactiva
   4.3 Dashboard administrativo
   4.4 PWA administrativa

5. Infraestructura y operación
   5.1 Contenedores de desarrollo
   5.2 Contenedores de producción
   5.3 Despliegue VPS/Dokploy
   5.4 CI y smoke tests
   5.5 Retención y limpieza operacional

6. Verificación y validación
   6.1 Tests automatizados, Ruff y mypy
   6.2 Validación E2E con Open-WA autenticado
   6.3 Rendimiento en 1 vCPU / 4 GB
   6.4 Evaluación WER rural
   6.5 Piloto de Traiguén
   6.6 Auditoría legal pre-escalamiento
```

## Diccionario de la EDT

| Paquete | Resultado y aceptación | Estado |
|---|---|---|
| 1.1–1.4 Dirección y alcance | Cuatro documentos coherentes, enlazados y sin atribuciones no verificadas | Ejecutado por este conjunto; aceptación académica pendiente |
| 2.1 API y SQLite | La aplicación inicia, persiste y expone rutas previstas sin DB externa | Implementado |
| 2.2 ODEPA | Sincroniza y consulta el catálogo; conserva último dato utilizable ante fallo y requiere contar el snapshot para afirmar 79 productos/15 mercados | Implementado; cardinalidad no verificable documentalmente |
| 2.3 OpenMeteo | Consulta clima por comuna y usa cache degradado cuando corresponde | Implementado |
| 2.4 Herramientas | Solo se ejecutan herramientas permitidas y la aritmética monetaria es determinística | Implementado |
| 2.5 Contexto de usuario | Preferencias y estado se asocian a identidad WhatsApp; historial requiere opt-in | Implementado según línea base; cierre de issues relacionado aún debe reconciliarse |
| 2.6 Alertas | Evalúa umbrales informativos, evita duplicados y respeta consentimiento/rate limit | Implementado |
| 2.7 Seguridad y observabilidad | Auth admin, logs sanitizados, métricas y monitor disponibles | Implementado; auditoría legal formal pendiente |
| 3.1 Open-WA | Webhook recibe eventos y el gateway puede entregar respuestas | Implementado; validación autenticada pendiente en [#214][i214] |
| 3.2 Texto | Mensaje escrito evita STT/TTS y recibe respuesta escrita | Implementado |
| 3.3 Audio/STT | Audio se convierte y transcribe localmente | Implementado; WER rural pendiente |
| 3.4 LLM/fallback | Modelo local usa tools; fallos tienen respuesta degradada y no matan el servicio | Implementado |
| 3.5 TTS/entrega | Texto se normaliza para voz, sintetiza y convierte a formato de WhatsApp | Implementado; entrega real se valida con 3.1 |
| 4.1 Landing | Build estático correcto y contenido del producto accesible | Implementado |
| 4.2 Demo | Permite demostrar consultas sin WhatsApp cuando el endpoint está habilitado | Implementado con restricción operativa conocida |
| 4.3–4.4 Admin | Ocho vistas, HTMX, Chart.js local y soporte PWA administrativo | Implementado |
| 5.1–5.4 Infraestructura | Entornos reproducibles, CI y configuración de despliegue documentada | Parcial; no hay evidencia local de despliegue efectivo |
| 5.5 Retención | Media temporal (audio/imagen) se elimina dentro del límite y datos sensibles se minimizan | Implementado en diseño y tests; observar en operación |
| 6.1 Calidad automatizada | Smoke CI con suite, linter y tipos; el resultado debe registrarse en el commit candidato | Control reproducible; resultado vigente no registrado |
| 6.2 E2E autenticado | Audio/texto real cruza WhatsApp y vuelve al usuario | Pendiente |
| 6.3 Piso degradado | Escenarios críticos cumplen umbrales en 1 vCPU / 4 GB | Benchmark pendiente en [#215][i215]; no lo sustituye el smoke CI |
| 6.4 WER | Muestra consentida rural obtiene WER menor a 15% | Pendiente del piloto |
| 6.5 Piloto | 3–5 productores completan cuatro semanas y se registran métricas | Planificado, no ejecutado |
| 6.6 Auditoría legal | Revisión formal previa al escalamiento | Pendiente |

## Criterios de aceptación del alcance

El alcance técnico se considera verificable cuando:

1. las rutas críticas y servicios pasan sus tests;
2. Ruff y mypy no reportan errores;
3. el build de la landing finaliza;
4. las consultas soportadas usan datos oficiales o informan honestamente la
   falta de datos;
5. un fallo de proveedor o modelo no expone secretos ni genera consejo
   inventado;
6. las pruebas de hardware y WhatsApp real registran resultados observados.

La aceptación del piloto exige además consentimiento, participantes reales,
medición de WER y métricas de uso. El kit documental por sí solo no satisface
ese criterio.

## Supuestos y dependencias

- El número de WhatsApp se mantiene disponible como identificador.
- ODEPA continúa publicando datos consumibles o recuperables por los fallbacks.
- OpenMeteo mantiene acceso gratuito suficiente para el piloto.
- El VPS dispone del almacenamiento y memoria definidos en la línea base.
- Los productores y la contraparte territorial aún deben confirmar
  participación; no se registran como comprometidos.

## Control del alcance

Cada solicitud se contrasta con esta EDT y con
[AGENTS.md](../../AGENTS.md). Si agrega un entregable, cambia una exclusión o
afecta un criterio de éxito, debe actualizar este documento y la
[Matriz de trazabilidad](./04-trazabilidad.md). El cierre de un issue sin
evidencia funcional no basta; del mismo modo, un artefacto implementado con
issue abierto requiere reconciliar GitHub antes del cierre formal.

## Verificación reproducible

```bash
# Inventario trazable
git ls-files 'backend/app/**/*.py'
git ls-files 'backend/tests/test_*.py'
git ls-files 'landing/src/**/*'

# Aceptación automatizada
cd backend
uv run pytest tests/ -v --tb=short
uv run ruff check app/
uv run mypy app/
cd ../landing
bun run build

# Validaciones aún abiertas
gh issue view 214 --repo sebitabravo/AgroVoz
gh issue view 215 --repo sebitabravo/AgroVoz
```

[d137]: https://github.com/sebitabravo/AgroVoz/discussions/137
[i172]: https://github.com/sebitabravo/AgroVoz/issues/172
[i214]: https://github.com/sebitabravo/AgroVoz/issues/214
[i215]: https://github.com/sebitabravo/AgroVoz/issues/215
