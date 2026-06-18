# AgroVoz — Dockerfile multi-stage para backend FastAPI
#
# Etapa 1 (builder): instala dependencias Python con cache de capas.
# Etapa 2 (runtime): instala ffmpeg + curl, copia venv del builder,
#                    ejecuta como usuario no-root con HEALTHCHECK.

# ── Etapa 1: Builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Instalar herramientas de compilación (necesarias para llama-cpp-python,
# whisper, piper-tts cuando se agreguen en fases posteriores)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        make \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Crear virtualenv aislado
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Instalar dependencias Python (cache de capas: requirements.txt cambia poco)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Etapa 2: Runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Instalar dependencias de sistema en runtime
# - ffmpeg: conversión .ogg ↔ .wav para pipeline de voz
# - curl: HEALTHCHECK y debugging
# - ca-certificates: SSL para APIs externas (OpenWeatherMap, ODEPA)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario no-root (seguridad: el proceso no corre como root)
RUN groupadd --system --gid 1000 agrovoz && \
    useradd --system --uid 1000 --gid agrovoz --create-home agrovoz

# Copiar virtualenv desde builder
COPY --from=builder --chown=agrovoz:agrovoz /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Directorio de trabajo
WORKDIR /app

# Copiar código de la aplicación
# .dockerignore excluye models/, data/, .env*, __pycache__, .git, etc.
COPY --chown=agrovoz:agrovoz . .

# Crear directorio data/ para SQLite y audio temporal (el volumen se monta aquí)
RUN mkdir -p /app/data && chown agrovoz:agrovoz /app/data

# Cambiar a usuario no-root
USER agrovoz

# Exponer puerto (Traefik/Nginx hace reverse proxy en prod; en dev se mapea al host)
EXPOSE 8000

# Health check: el endpoint /api/v1/health retorna {"status": "ok"} si la app
# y la DB responden correctamente.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Entry point: uvicorn con workers ajustados al VPS (8 vCPU → 4 workers en prod)
# En dev se puede sobreescribir con --reload vía docker-compose command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
