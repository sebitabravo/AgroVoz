# Smoke E2E de WhatsApp con Open-WA

Runbook local para validar integración real. El protocolo completo de cierre y
la matriz de evidencia están en [`docs/validacion-operativa.md`](validacion-operativa.md).
No reemplaza una validación productiva ni acredita piloto.

## Preparación

```bash
cp .env.example .env
# Configurar OPENWA_API_KEY, OPENWA_WEBHOOK_SECRET y PHONE_HASH_PEPPER.
make up
```

Abrir `http://localhost:2785/` y escanear QR con un teléfono de prueba. La sesión queda persistida en el volumen Docker `openwa_data`.

Verificar salud:

```bash
curl -fsS http://localhost:8000/api/v1/health
curl -fsS http://localhost:2785/api/health
```

## Casos

Enviar desde el teléfono de prueba, uno por vez:

1. Texto: `¿A cuánto está la papa en Estación Central?`
2. Texto: `¿Qué clima habrá en Traiguén?`
3. Texto: `¿Cuánto recibo por 30 kilos de papa?`
4. Texto: `¿Dónde queda INDAP en Traiguén?`
5. Nota de voz con una pregunta de precio.
6. Ubicación compartida, solo si `LOCATION_SHARING_ENABLED=true` y existe consentimiento.
7. Foto, solo si el modelo local de visión está provisionado y `VISION_ENABLED=true`.

Mantener el volumen bajo durante la prueba. No usar números de terceros ni datos personales innecesarios.

## Evidencia

```bash
docker compose logs --since=10m backend openwa
sqlite3 backend/data/agrovoz.db \
  'SELECT intent, latency_ms, delivery_status, delivery_error_code FROM consultations ORDER BY id DESC LIMIT 10;'
```

Guardar fecha, versión/commit, caso, latencia observada, estado `pending`/`delivered`/`failed` y respuesta recibida. No guardar audios, teléfonos, tokens ni transcripciones en el repositorio.

## Limitaciones

- Ejecución local no demuestra operación productiva.
- Latencia del Mac no demuestra el piso 1 vCPU / 4 GB.
- La sesión QR y el número de prueba requieren autorización del operador.
- Si Open-WA no está autenticado, el resultado válido es `blocked`, no `passed`.
