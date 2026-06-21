"""Configuración centralizada con pydantic-settings.

Lee variables de entorno desde .env (desarrollo) o entorno real (producción).
"""

import warnings
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
        extra="ignore",  # Tolera vars del sistema (PATH, HOME, TZ, vars de Dokploy/Traefik)
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
    openwa_webhook_secret: str = "dev-webhook-secret"
    openwa_api_url: str = "http://localhost:2785"

    # ── OpenWeatherMap ───────────────────
    openweathermap_api_key: str = ""

    # ── ODEPA ────────────────────────────
    odepa_sync_hour: int = 6
    odepa_sync_minute: int = 0

    # ── Modelos IA ───────────────────────
    whisper_model: str = "small"
    whisper_model_path: str = ""  # Directorio para modelos Whisper (vacio = default ~/.cache/whisper/)
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
    extra_allowed_hosts: str = ""  # Hosts/IPs extra separadas por coma para TrustedHostMiddleware

    # ── Versión ──────────────────────────
    app_version: str = "0.1.0-dev"

    # ── Logging ──────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    def validate_webhook_secret_not_default(self) -> None:
        """Advierte o bloquea si openwa_webhook_secret es el default público o está vacío.

        En development el default es aceptable.
        En producción lanza ValueError (bloquea el arranque).
        En test/CI emite RuntimeWarning (no bloquea tests).

        El guard de secret vacío previene que Docker Compose pase ""
        cuando OPENWA_WEBHOOK_SECRET no está seteado en Dokploy.
        """
        _default_secret = "dev-webhook-secret"

        if not self.openwa_webhook_secret:
            if self.app_env == "production":
                raise ValueError(
                    "OPENWA_WEBHOOK_SECRET está vacío. "
                    "Debe setear OPENWA_WEBHOOK_SECRET con un valor secreto "
                    "antes de desplegar a producción."
                )
            if self.app_env != "development":
                warnings.warn(
                    "OPENWA_WEBHOOK_SECRET está vacío. Cámbielo antes de desplegar a producción.",
                    RuntimeWarning,
                    stacklevel=2,
                )
            return

        if self.openwa_webhook_secret != _default_secret:
            return  # Secret personalizado, todo OK

        if self.app_env == "production":
            raise ValueError(
                "OPENWA_WEBHOOK_SECRET es el valor default público. "
                "Debe setear OPENWA_WEBHOOK_SECRET con un valor secreto "
                "antes de desplegar a producción."
            )
        if self.app_env != "development":
            warnings.warn(
                "OPENWA_WEBHOOK_SECRET es el valor default público. Cámbielo antes de desplegar a producción.",
                RuntimeWarning,
                stacklevel=2,
            )

    def validate_pepper_not_default(self) -> None:
        """Advierte o bloquea si phone_hash_pepper es el default público o está vacío.

        Siempre emite RuntimeWarning si el pepper es default o está vacío
        (incluyendo development, para que el equipo sepa que debe cambiarlo).
        En producción lanza ValueError (bloquea el arranque).

        El guard de pepper vacío previene que Docker Compose pase ""
        cuando PHONE_HASH_PEPPER no está seteado en Dokploy.
        """
        _default_pepper = "agrovoz-dev-pepper"

        if not self.phone_hash_pepper:
            if self.app_env == "production":
                raise ValueError(
                    "PHONE_HASH_PEPPER está vacío. "
                    "Debe setear PHONE_HASH_PEPPER con un valor secreto "
                    "antes de desplegar a producción."
                )
            warnings.warn(
                "PHONE_HASH_PEPPER está vacío. Cámbielo antes de desplegar a producción.",
                RuntimeWarning,
                stacklevel=2,
            )
            return

        if self.phone_hash_pepper != _default_pepper:
            return  # Pepper personalizado, todo OK

        if self.app_env == "production":
            raise ValueError(
                "PHONE_HASH_PEPPER es el valor default público. "
                "Debe setear PHONE_HASH_PEPPER con un valor secreto "
                "antes de desplegar a producción."
            )
        warnings.warn(
            "PHONE_HASH_PEPPER es el valor default público. Cámbielo antes de desplegar a producción.",
            RuntimeWarning,
            stacklevel=2,
        )

    def validate_api_keys_in_dev(self) -> None:
        """Advierte si las API keys requeridas están vacías en development.

        En producción, main.py lanza ValueError antes de arrancar (fail-fast).
        En development, emitir warnings para que el equipo no pierda horas
        debugueando llamadas silenciosamente sin autenticación.
        """
        if self.app_env != "development":
            return

        if not self.openwa_api_key:
            warnings.warn(
                "OPENWA_API_KEY no está configurada. "
                "Las llamadas a Open-WA no tendrán autenticación (X-API-Key). "
                "El pipeline de audio no funcionará sin esto.",
                RuntimeWarning,
                stacklevel=2,
            )
        if not self.openweathermap_api_key:
            warnings.warn(
                "OPENWEATHERMAP_API_KEY no está configurada. Las consultas de clima no funcionarán sin esto.",
                RuntimeWarning,
                stacklevel=2,
            )


settings = Settings()
settings.validate_webhook_secret_not_default()
settings.validate_pepper_not_default()
settings.validate_api_keys_in_dev()
