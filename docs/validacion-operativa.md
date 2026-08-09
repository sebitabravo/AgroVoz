# Validación operativa E2E de Open-WA

Procedimiento reutilizable para el issue [#214](https://github.com/sebitabravo/AgroVoz/issues/214).
El objetivo es comprobar el camino real WhatsApp → Open-WA → FastAPI →
Whisper/LLM/Piper → Open-WA, sin confundir los tests con mocks o un simple
health check.

## Regla de evidencia

Una ejecución real necesita una `OPENWA_API_KEY` operativa y una sesión
WhatsApp autenticada. No se deben inventar credenciales, números, mensajes,
resultados, latencias ni logs. El archivo JSON del preflight es solo un
resumen técnico saneado y no certifica los casos conversacionales.

Estado de este PR (2026-08-03): **BLOQUEADO**. El entorno de trabajo no tiene
`OPENWA_API_KEY` ni una sesión Open-WA autenticada; por eso los casos de la
matriz quedan **NO EJECUTADOS** y no se adjunta evidencia ficticia.

## Precondiciones

- Número WhatsApp de prueba, separado del número personal del equipo.
- Open-WA y AgroVoz levantados en el mismo entorno que se quiere validar.
- `OPENWA_API_KEY` y `OPENWA_WEBHOOK_SECRET` cargados desde el gestor de
  secretos o desde un `.env` local fuera de Git.
- `OPENWA_API_URL` y `AGROVOZ_E2E_BASE_URL` apuntando al entorno bajo prueba.
- Un operador con acceso al teléfono de prueba para enviar texto y notas de
  voz al número de AgroVoz.
- ODEPA sincronizado y OpenMeteo accesible. Si una fuente está caída, la
  respuesta debe declararlo y el caso no se marca como pasado por intuición.

Nunca pegues una API key, un QR, un teléfono, un `chatId`, una transcripción o
audio crudo en el repositorio, en la PR o en un log compartido.

## 1. Preparar y comprobar la sesión

En desarrollo local:

```bash
make up
docker compose ps
```

1. Abrir el dashboard local de Open-WA y crear o seleccionar la sesión de
   prueba.
2. Escanear el QR con el teléfono de prueba y esperar un estado de sesión
   `ready` o `active`.
3. Confirmar que el webhook apunta a
   `/api/v1/webhook/whatsapp` y que su secreto coincide con
   `OPENWA_WEBHOOK_SECRET`.
4. No capturar ni guardar el QR o la pantalla de WhatsApp como evidencia.

El preflight automatizado valida liveness del backend y que Open-WA responda
con al menos una sesión lista. No envía mensajes y no sustituye la validación
del teléfono:

```bash
cd backend
test -n "${OPENWA_API_KEY:-}" || {
  echo "BLOQUEADO: falta OPENWA_API_KEY" >&2
  exit 2
}
uv run python scripts/openwa_e2e_preflight.py \
  --evidence data/openwa_e2e_preflight.json
```

Los códigos de salida son:

| Código | Significado |
|---:|---|
| `0` | Backend y sesión Open-WA listos; todavía faltan los casos conversacionales. |
| `1` | Fallo verificable de infraestructura o formato de respuesta. |
| `2` | Bloqueo por configuración o sesión no autenticada. |

El artefacto queda en `backend/data/`, que está excluido de Git. Comparte
solamente el JSON saneado si hace falta adjuntarlo a una incidencia.

## 2. Matriz de casos críticos

Para cada caso, el operador anota hora de inicio al enviar el mensaje y hora
de fin cuando la respuesta aparece en WhatsApp. La latencia E2E es esa
duración completa y debe ser menor a 15 segundos. Los campos **Observado** y
**Latencia** se dejan vacíos hasta contar con una corrida real.

| ID | Canal y entrada | Resultado esperado verificable | Observado | Latencia |
|---|---|---|---|---|
| `TXT-PRECIO` | Texto: `¿A cuánto está la papa en Temuco?` | Precio crudo de ODEPA con producto, mercado, unidad, fecha y fuente; sin interpretar rentabilidad. | `PENDIENTE` | `PENDIENTE` |
| `TXT-CLIMA` | Texto: `¿Cómo estará el tiempo mañana en Traiguén?` | Pronóstico de OpenMeteo para la fecha y ubicación solicitadas, con su fecha de consulta; sin recomendar labores. | `PENDIENTE` | `PENDIENTE` |
| `TXT-VENTA` | Texto: `Vendí 100 kilos de papa a $800 cada uno.` | Extracción determinista de producto, cantidad, unidad y precio; si compara con ODEPA, incluye fuente/fecha y no inventa faltantes. | `PENDIENTE` | `PENDIENTE` |
| `TXT-COMPUESTA` | Texto: `¿A cuánto está la papa en Temuco y cómo estará el tiempo mañana en Traiguén?` | Entrega ambos datos, cada uno con su fuente y fecha, sin mezclar precio con clima. | `PENDIENTE` | `PENDIENTE` |
| `AUD-PRECIO` | Nota de voz leyendo la consulta de `TXT-PRECIO`. | Evento `voice` recibido; Whisper conserva producto/mercado; respuesta enviada como audio por Open-WA. | `PENDIENTE` | `PENDIENTE` |
| `AUD-COMPUESTA` | Nota de voz leyendo la consulta de `TXT-COMPUESTA`. | Se observa la cadena Whisper → tools deterministas → respuesta → Piper/TTS → envío de audio; latencia total menor a 15 s. | `PENDIENTE` | `PENDIENTE` |

Las frases son estímulos repetibles, no resultados esperados. El precio,
pronóstico y fecha observados deben salir de las fuentes del momento de la
corrida. En cualquier consulta agronómica que no tenga una regla oficial
vigente, el resultado esperado es una negativa honesta con la ausencia del
dato; el LLM no puede completar el vacío.

## 3. Registrar evidencia sin PII

Por caso registra únicamente:

- `case_id`, fecha/hora UTC de inicio y fin, `latency_ms` y
  `passed|failed|blocked`.
- `request_id` o un identificador técnico ya saneado; nunca el teléfono ni el
  `chatId` completo.
- Tipo de evento (`chat` o `voice`), tamaño de audio en bytes y códigos de
  entrega, sin guardar audio, base64, texto libre ni transcripción.
- Para precio, clima o regla agronómica: fuente y fecha que aparecieron en la
  respuesta, sin copiar el contenido conversacional completo.
- Error cerrado y acción de mitigación cuando el caso falle.

Los logs relevantes se revisan en el intervalo de la corrida, pero no se
copian crudos a una PR:

```bash
docker compose logs --since <inicio-utc> backend openwa
```

Conserva solo líneas saneadas de etapa, código, latencia y estado de entrega.
El audio temporal y cualquier export de prueba se elimina antes de 24 horas;
no se reinicia el volumen de Open-WA a ciegas porque contiene la sesión
autenticada.

## 4. Diagnóstico y mitigación

| Señal | Diagnóstico seguro | Acción |
|---|---|---|
| Falta `OPENWA_API_KEY` | Configuración incompleta; no es un resultado E2E. | Cargarla desde secretos y repetir el preflight. No escribirla en el repo. |
| HTTP 401/403 de Open-WA | API key rechazada o sesión/secretos desalineados. | Revisar la key administrada y la sesión; no pegar la respuesta completa en logs compartidos. |
| `session_not_ready` | QR no escaneado, WhatsApp desconectado o sesión expirada. | Reconectar desde el dashboard y repetir; no borrar el volumen sin autorización. |
| No llega el webhook | URL, conectividad entre contenedores o HMAC incorrectos. | Revisar URL, `OPENWA_WEBHOOK_SECRET` y logs saneados del gateway. |
| Llega webhook pero no responde | Fallo en Whisper/LLM/Piper, SQLite o entrega. | Correlacionar por `request_id`, guardar solo el código de error y revisar la etapa. |
| Latencia ≥ 15 s | El gate de rendimiento falla aunque el mensaje llegue. | Medir etapas, validar en el piso de hardware vigente y documentar mitigación antes de pilotar. |
| Regla agronómica sin fuente vigente | No hay base determinista para aconsejar. | La respuesta correcta es indicar que no hay dato; no aceptar una frase improvisada del LLM. |

## 5. Criterio de cierre

El issue puede marcarse completo solamente cuando todos los casos críticos
tengan resultado observado, latencia medida y logs saneados, y no exista un
error abierto sin mitigación. Un preflight verde, los tests unitarios o un
webhook enviado directamente con `curl` no demuestran por sí solos que
WhatsApp real haya recibido y devuelto el mensaje.

### Estado de esta validación

- [ ] `OPENWA_API_KEY` operativa y sesión autenticada.
- [ ] Casos de texto (`TXT-*`) ejecutados.
- [ ] Casos de audio (`AUD-*`) ejecutados con Whisper, tools, TTS y entrega.
- [ ] Latencias registradas y todas menores a 15 s.
- [ ] Logs saneados y errores/mitigaciones documentados.
- [ ] Corrida repetible disponible para el equipo de piloto.

Mientras falten credenciales o sesión, el estado correcto es **BLOQUEADO**,
no `passed`.
