# Fase 02: Pipeline de Voz — Whisper + LLM + TTS

**Objetivo**: Implementar el pipeline end-to-end de procesamiento de voz:
audio → transcripción → comprensión NL + Tool Calling → síntesis de voz.
Sin integración Open-WA aún (se prueba con archivos locales).
**Duración estimada**: 6 tareas
**Dependencias**: Fase 01 completada
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Tareas

### T2.1: Servicio Whisper — Transcripción de audio

- [ ] Crear `backend/app/services/whisper_service.py`:
  - `load_model(model_name)` — carga Whisper `small` o `tiny` (configurable). Singleton.
  - `transcribe(audio_path)` — transcribe archivo .wav a texto
  - `preprocess_audio(input_path)` — convierte .ogg → .wav 16kHz mono con ffmpeg
  - Wrapper class `WhisperService` con lazy loading del modelo
  - Manejo de errores: archivo corrupto, audio vacío, timeout
- [ ] Agregar `WHISPER_MODEL=small` a `.env.example`
- [ ] Descargar modelo en Dockerfile (caché entre builds):
  ```dockerfile
  RUN python -c "import whisper; whisper.load_model('small')"
  ```
- Archivos a crear: `app/services/whisper_service.py`
- Test: `backend/tests/test_whisper_service.py` (usar audio sintético con TTS Piper)

### T2.2: Servicio TTS — Síntesis de voz

- [ ] Crear `backend/app/services/tts_service.py`:
  - `load_model()` — carga Piper TTS voz español. Singleton.
  - `synthesize(text, output_path)` — texto → archivo .wav
  - `postprocess_audio(input_path, output_path)` — .wav → .ogg (WhatsApp)
  - Wrapper class `TTSService` con lazy loading
  - Cache de respuestas frecuentes: "No tengo ese dato", "No entendí, ¿puedes repetir?"
- [ ] Descargar voz española de Piper en Dockerfile
- [ ] Crear `backend/app/services/audio_utils.py`:
  - `convert_audio(input, output, format)` — wrapper sobre ffmpeg
  - `get_audio_duration(path)` — duración en ms
  - `validate_audio(path)` — chequea que no esté vacío/corrupto
- Archivos a crear: `app/services/tts_service.py`, `app/services/audio_utils.py`
- Test: `backend/tests/test_tts_service.py`

### T2.3: Servicio LLM — Comprensión de lenguaje

- [ ] Crear `backend/app/services/llm_service.py`:
  - `load_model()` — carga LLM cuantizado (llama-cpp-python). Singleton.
  - `generate_response(query_text, context)` — genera respuesta textual
  - System prompt estricto (ver abajo)
  - Tool Calling con whitelist:
    - Herramienta `get_price(producto, mercado?)` → llama a `odepa_service.query_prices()`
    - Herramienta `get_weather(lat, lon)` → llama a `weather_service.get_current_weather()`
    - Si el modelo intenta usar herramienta no whitelisteada → fallback: "No tengo ese dato, pero puedo consultarte el precio en ODEPA o el clima."
- [ ] System prompt del LLM (ESENCIAL):
  ```
  Eres AgroVoz, un asistente de voz para pequeños agricultores chilenos.
  REGLAS ESTRICTAS:
  1. SOLO entregas datos de precios (ODEPA) y clima (OpenWeatherMap).
  2. NUNCA das recomendaciones agronómicas. Si preguntan "¿debo regar?",
     responde con el pronóstico de lluvia, sin interpretar.
  3. NUNCA inventas precios ni clima. Si no tienes el dato, lo dices.
  4. Respondes en español chileno, con frases cortas y claras (máximo 3 oraciones).
  5. Los precios se dan en pesos chilenos, con la unidad de medida.
  6. Si no entiendes la pregunta, pides que la reformulen.
  ```
- [ ] Agregar `LLM_MODEL_PATH=models/qwen2.5-3b-q4_k_m.gguf` a `.env.example`
- Archivos a crear: `app/services/llm_service.py`
- Test: `backend/tests/test_llm_service.py`

### T2.4: Pipeline Service — Orquestador end-to-end

- [ ] Crear `backend/app/services/pipeline_service.py`:
  - `class AgroVozPipeline`:
    - `__init__()` — inicializa WhisperService, LLMService, TTSService
    - `async process_audio(audio_bytes, phone_number)` → `AudioResponse`
    - Flujo completo:
      1. Guardar audio temporal (nombre único: UUID)
      2. `whisper_service.transcribe()` → texto
      3. `llm_service.generate_response()` → respuesta textual (con Tool Calling)
      4. `tts_service.synthesize()` → audio respuesta
      5. Guardar consulta en `consultations` table (métricas)
      6. Limpiar archivos temporales
      7. Retornar audio + metadata
  - `cleanup_old_audio()` — borra archivos >24h (se llamará en cron)
  - Timeout global: 20 segundos → si se excede, responder con mensaje de error pre-grabado
- [ ] Definir DTOs en `backend/app/schemas/pipeline.py`:
  - `AudioResponse(audio_path, text_response, latency_ms, intent)`
- Archivos a crear: `app/services/pipeline_service.py`, `app/schemas/pipeline.py`
- Test: `backend/tests/test_pipeline_service.py`

### T2.5: Descarga de modelos — Scripts y Docker

- [ ] Crear `backend/scripts/download_models.sh`:
  - Descarga Whisper `small` (ya lo maneja whisper automáticamente)
  - Descarga LLM Qwen2.5-3B Q4_K_M desde HuggingFace
  - Descarga voz Piper español
- [ ] Actualizar `backend/Dockerfile` para pre-descargar modelos:
  - Los modelos se almacenan en `/app/models/` (volumen en docker-compose)
  - Capa separada para cache de build
- [ ] Agregar `backend/models/.gitkeep` (modelos están en .gitignore)
- Archivos a crear: `scripts/download_models.sh`
- Archivos a modificar: `backend/Dockerfile`

### T2.6: Integración y tests de pipeline

- [ ] Crear `backend/tests/fixtures/sample_query.ogg` — audio real preguntando "¿A cuánto está la papa en Santiago?"
- [ ] Test end-to-end: audio real → transcripción → LLM → TTS → audio respuesta
- [ ] Test de fallback: "¿Qué fecha es hoy?" → el LLM debe responder que no tiene ese dato
- [ ] Test de Tool Calling: verificar que `get_price` y `get_weather` se invocan correctamente
- [ ] Test de timeout: audio muy largo → debe responder en <20s
- [ ] Medir latencia de cada etapa y loguear
- Archivos a crear: `tests/fixtures/sample_query.ogg`
- Test: `backend/tests/test_pipeline_e2e.py`

---

## Validación

- Checklist: `docs/validation/02-pipeline-checklist.md`
- Comandos:
  ```bash
  cd backend && python -m pytest tests/ -v -k "pipeline"
  cd backend && python -m pytest tests/ -v --cov=app/services --cov-report=term-missing
  cd backend && ruff check app/services/
  cd backend && mypy app/services/
  ```

## Output esperado

```
backend/app/services/
├── __init__.py
├── audio_utils.py
├── whisper_service.py
├── llm_service.py
├── tts_service.py
├── pipeline_service.py
├── odepa_service.py      (existente, Fase 01)
└── weather_service.py    (existente, Fase 01)

backend/app/schemas/
├── __init__.py
└── pipeline.py

backend/scripts/
├── create_db.py          (existente, Fase 00)
└── download_models.sh

backend/tests/
├── test_whisper_service.py
├── test_llm_service.py
├── test_tts_service.py
├── test_pipeline_service.py
├── test_pipeline_e2e.py
└── fixtures/
    └── sample_query.ogg
```

---

## Estado de implementación

**Fase completada.** Verificado contra `main` (PR #51 — Qwen2.5-3B Q4 + Tool Calling). Desviaciones respecto al spec:

- **Audio utils**: se llama `audio_service.py` (no `audio_utils.py`). Misma responsabilidad (convert/duration/validate con ffmpeg).
- **LLM**: Qwen2.5-3B-Instruct Q4_K_M vía llama-cpp-python con **Tool Calling real** (no keyword matching). Whitelist de tools: `get_price`, `get_weather`. PR #51 mergeado.
- **Stage timing**: migración `8f2a4c7e1d90` agrega `whisper_ms`/`llm_ms`/`tts_ms` a `Consultation` para desglose de latencia por etapa.
- **Descarga de modelos**: `scripts/download_models.sh` + `scripts/entrypoint.sh` idempotente (descarga Whisper/Qwen GGUF/Piper ONNX al arranque si no están). PR #64.
- **Preload LLM**: el lifespan precarga el modelo LLM en background para evitar cold start en el primer request.
- **Fixture**: `sample_query.ogg` en `backend/tests/fixtures/` confirmado.
- **Tests**: `test_whisper_service.py`, `test_llm_service.py`, `test_tts_service.py`, `test_pipeline_service.py`, `test_pipeline_e2e.py`, `test_tts_benchmark.py`.
