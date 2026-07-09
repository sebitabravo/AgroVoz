# Fase 03: Integración Open-WA — WhatsApp Webhook Self-Hosted

**Objetivo**: Conectar el pipeline de voz con WhatsApp a través de Open-WA,
un gateway WhatsApp self-hosted y gratuito que corre en el mismo VPS.
El agricultor envía audio por WhatsApp → AgroVoz responde con audio.
**Duración estimada**: 5 tareas
**Dependencias**: Fase 02 completada
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Contexto técnico

**Open-WA** es un gateway WhatsApp HTTP API que corre localmente. Usa el protocolo
WhatsApp Web (QR scan) — no requiere WhatsApp Business API ni Meta approval.
Es MIT license, 100% gratuito, sin límites de mensajes.

**Stack de Open-WA:** Node.js 22, NestJS 11, TypeScript, PostgreSQL/SQLite, Redis.

**Flujo:**
1. Agricultor envía audio al número WhatsApp conectado a Open-WA
2. Open-WA recibe el mensaje → dispara webhook a FastAPI
3. FastAPI procesa el audio (pipeline de voz)
4. FastAPI envía audio respuesta vía Open-WA REST API
5. Agricultor recibe respuesta por WhatsApp

**Riesgo aceptado:** Open-WA usa protocolo no-oficial de WhatsApp Web. Meta puede
banear el número si detecta uso automatizado masivo (>~100 msg/día). Para MVP con
3-5 productores esto no es problema. Para producción → migrar a WhatsApp Cloud API.

---

## Tareas

### T3.1: Servicio Open-WA en Docker

- [ ] Agregar servicio `openwa` en `docker-compose.yml`:
  ```yaml
  openwa:
    image: ghcr.io/rmyndharis/openwa:latest
    container_name: agrovoz-openwa
    ports:
      - "2785:2785"   # REST API
      - "2886:2886"   # Dashboard web
    environment:
      - NODE_ENV=production
      - DATABASE_PROVIDER=sqlite
      - API_KEY=${OPENWA_API_KEY}
      - WEBHOOK_URL=http://backend:8000/api/v1/webhook/whatsapp
    volumes:
      - openwa_data:/app/data
    restart: unless-stopped
    networks:
      - agrovoz
  ```
- [ ] Agregar volumen `openwa_data` en `docker-compose.yml`
- [ ] Agregar `OPENWA_API_KEY=<generar-uuid>` a `.env.example`
- [ ] Agregar `OPENWA_API_URL=http://openwa:2785` a `.env.example`
- [ ] Documentar setup inicial en `docs/DEV-GUIDE.md`:
  - Levantar Open-WA con `docker compose up -d openwa`
  - Abrir dashboard en `http://localhost:2886`
  - Crear sesión, escanear QR con WhatsApp del teléfono
  - Verificar que la sesión queda activa ("connected")
  - Probar: `curl -X POST http://localhost:2785/api/sessions/default/messages/send-text -H "X-API-Key: $OPENWA_API_KEY" -H "Content-Type: application/json" -d '{"phone":"+569XXXXXXXX","text":"Hola desde AgroVoz"}'`
- Archivos a modificar: `docker-compose.yml`, `.env.example`
- Archivos a crear: `docs/DEV-GUIDE.md`

### T3.2: Cliente Open-WA (httpx)

- [ ] Crear `backend/app/services/openwa_service.py`:
  - `class OpenWAService`:
    - `__init__()` — lee `OPENWA_API_URL` y `OPENWA_API_KEY` de config
    - `async send_text(phone, message)` → POST `/api/sessions/default/messages/send-text`
    - `async send_audio(phone, audio_url, caption?)` → POST `/api/sessions/default/messages/send-audio`
    - `async download_media(message_id)` → GET `/api/sessions/default/messages/{id}/media`
    - `async get_session_status()` → GET `/api/sessions/default`
  - Rate limiting interno: máximo 10 requests/minuto a Open-WA API
  - Retry con backoff exponencial (3 intentos) si Open-WA no responde
  - Logging de cada request: número hasheado, timestamp, latencia
- Archivos a crear: `app/services/openwa_service.py`
- Test: `backend/tests/test_openwa_service.py`

### T3.3: Endpoint webhook de WhatsApp

- [ ] Crear `backend/app/api/webhooks.py`:
  - `POST /api/v1/webhook/whatsapp` — endpoint que recibe webhook de Open-WA
  - Extrae del payload de Open-WA:
    - `from` (número WhatsApp del agricultor)
    - `body` (texto del mensaje, si es texto)
    - `media` (si tiene audio adjunto: mimetype, url de descarga)
    - `timestamp` (cuándo se envió)
  - Si es mensaje de texto → responde con mensaje de ayuda
  - Si es audio → llama a `pipeline_service.process_audio()`
  - Si es otro tipo → responde "Solo puedo recibir mensajes de voz o texto"
  - Envía respuesta vía `openwa_service.send_audio()` o `openwa_service.send_text()`
  - Logging de cada request (número hasheado, timestamp, latencia)
- [ ] Formato esperado del webhook de Open-WA:
  ```json
  {
    "sessionId": "default",
    "message": {
      "id": "msg_abc123",
      "from": "569XXXXXXXX",
      "body": "¿A cuánto está la papa?",
      "hasMedia": true,
      "media": [{
        "mimetype": "audio/ogg; codecs=opus",
        "url": "http://openwa:2785/api/sessions/default/messages/msg_abc123/media"
      }],
      "timestamp": 1718400000
    }
  }
  ```
- Archivos a crear: `app/api/webhooks.py`
- Archivos a modificar: `app/main.py` (registrar router)

### T3.4: Seguridad del webhook

- [ ] Implementar `backend/app/core/security.py`:
  - `validate_openwa_hmac(request, api_key)` — valida firma HMAC de Open-WA
  - `hash_phone(phone_number)` — SHA256 del número para anonimizar
  - `rate_limit_phone(phone_hash)` — máximo 10 consultas/minuto por número
  - Dependency `verify_openwa_webhook` para FastAPI
- [ ] Open-WA envía header `X-OpenWA-Signature` (HMAC SHA256 del body)
- [ ] Middleware de rate limiting global: 60 requests/minuto total
- Archivos a crear: `app/core/security.py`
- Test: `backend/tests/test_security.py`

### T3.5: Tests de integración Open-WA

- [ ] Crear `backend/tests/test_webhook.py`:
  - Test: POST con payload simulado de Open-WA
  - Test: validación de firma HMAC (con mock)
  - Test: rate limiting
  - Test: error handling (sin media, payload inválido)
  - Test: mensaje de texto → respuesta de ayuda
  - Test: mensaje de audio → pipeline procesa y responde
- [ ] Crear `backend/tests/test_openwa_service.py`:
  - Test: send_text exitoso
  - Test: send_audio exitoso
  - Test: retry en fallo de Open-WA
  - Test: timeout de Open-WA
- [ ] Crear `backend/tests/fixtures/openwa_webhook_payload.json`:
  - Payload de webhook realista (audio y texto)
- Comando: `cd backend && python -m pytest tests/test_webhook.py tests/test_openwa_service.py -v`

---

## Validación

- Checklist: `docs/validation/03-integration-checklist.md`
- Comandos:
  ```bash
  cd backend && python -m pytest tests/ -v
  cd backend && ruff check app/api/webhooks.py app/core/security.py app/services/openwa_service.py
  # Test manual: levantar Open-WA, escanear QR, enviar audio desde WhatsApp
  ```

## Output esperado

```
backend/app/api/
├── __init__.py
├── health.py              (existente)
├── prices.py              (existente)
├── weather.py             (existente)
├── webhooks.py            (NUEVO)
└── audio.py               (NUEVO)

backend/app/core/
├── __init__.py
├── config.py
├── database.py
└── security.py            (NUEVO — HMAC Open-WA, no Twilio)

backend/app/services/
├── __init__.py
├── odepa_service.py       (existente)
├── weather_service.py     (existente)
├── whisper_service.py     (existente)
├── llm_service.py         (existente)
├── tts_service.py         (existente)
├── pipeline_service.py    (existente)
└── openwa_service.py      (NUEVO)

backend/tests/
├── test_openwa_service.py (NUEVO)
├── test_webhook.py        (NUEVO)
└── fixtures/
    └── openwa_webhook_payload.json (NUEVO)

docker-compose.yml         (modificado: +servicio openwa +volumen)

docs/
└── DEV-GUIDE.md           (NUEVO)
```

---

## Estado de implementación

**Fase completada.** Verificado contra `main`. Desviaciones respecto al spec:

- **Phone hashing**: además de validar firma HMAC del webhook, los números se anonimizan con HMAC-SHA256 + pepper (`phone_hash.py`). Pepper validado en arranque prod (`validate_pepper_not_default`).
- **Rate limiting**: middleware global `RateLimitMiddleware` (no solo por número). Reset entre tests vía `reset_rate_limiter_for_tests()` en `conftest.py`.
- **Security headers + TrustedHost**: además de HMAC, hay `SecurityHeadersMiddleware` y `TrustedHostMiddleware`.
- **Request ID**: `RequestIDMiddleware` inyecta `X-Request-ID` en cada request para trazabilidad en logs.
- **Fixture**: `openwa_webhook_payload.json` en `backend/tests/fixtures/` confirmado.
- **Docs**: `docs/DEV-GUIDE.md` confirmado.
- **Tests**: `test_webhook.py`, `test_openwa_service.py`, `test_security.py`, `test_rate_limiter.py`.
