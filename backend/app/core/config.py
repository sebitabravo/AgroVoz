"""Configuración centralizada con pydantic-settings.

Lee variables de entorno desde .env (desarrollo) o entorno real (producción).
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Path absoluto a data/ para que la DB no dependa del CWD desde donde se lance uvicorn.
# Resuelve desde este archivo: backend/app/core/config.py -> backend/ -> raíz del repo
_data_dir = Path(__file__).resolve().parent.parent.parent / "data"
_data_dir.mkdir(parents=True, exist_ok=True)

DEFAULT_DATABASE_URL = f"sqlite:///{_data_dir / 'agrovoz.db'}"


class Settings(BaseSettings):
    """Configuración global de AgroVoz.

    Cada campo tiene un default seguro para desarrollo.
    En producción, se sobreescribe con variables de entorno reales.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",  # Rechaza variables de entorno desconocidas (typo-safety)
    )

    # ── Entorno ──────────────────────────
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = True

    # ── Base de datos ────────────────────
    database_url: str = DEFAULT_DATABASE_URL

    # ── Servidor ─────────────────────────
    host: str = "127.0.0.1"  # Solo localhost por defecto. En prod: reverse proxy (Traefik/Nginx)
    port: int = 8000

    # ── Open-WA (gateway WhatsApp) ───────
    openwa_api_key: str = ""
    openwa_webhook_secret: str = ""
    openwa_api_url: str = "http://localhost:3000"

    # ── OpenWeatherMap ───────────────────
    openweathermap_api_key: str = ""

    # ── ODEPA ────────────────────────────
    odepa_sync_hour: int = 6
    odepa_sync_minute: int = 0

    # ── Modelos IA ───────────────────────
    whisper_model: str = "small"
    llm_model_path: str = "models/qwen2.5-3b-q4_k_m.gguf"
    piper_voice: str = "es_ES-carlfm-x_low"

    # ── Admin ────────────────────────────
    admin_api_key: str = ""

    # ── Modelos IA (paths) ───────────────
    piper_model_path: str = "/app/models/es_ES-carlfm-x_low.voice"

    # ── Seguridad ────────────────────────
    rate_limit_per_minute: int = 60
    audio_retention_hours: int = 24
    phone_hash_pepper: str = "agrovoz-dev-pepper"  # Cambiar en producción (PHONE_HASH_PEPPER en .env)

    # ── Versión ──────────────────────────
    app_version: str = "0.1.0-dev"

    # ── Logging ──────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


settings = Settings()
