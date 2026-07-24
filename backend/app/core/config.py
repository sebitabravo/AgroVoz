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

    # ── OpenWeatherMap (DEPRECATED) ───────
    # Ya no se usa. Migrado a OpenMeteo (sin API key) en issue #51.
    # Se mantiene por compatibilidad, pero no afecta el funcionamiento.
    openweathermap_api_key: str = ""

    # ── OpenMeteo / clima ────────────────
    # Tope de antigüedad del cache degradado cuando OpenMeteo falla.
    # Si la API no responde, se entrega el último pronóstico cacheado
    # siempre que tenga menos horas que este valor. Más viejo → error honesto.
    weather_stale_cache_max_age_hours: int = 6

    # ── ODEPA ────────────────────────────
    odepa_sync_hour: int = 6
    odepa_sync_minute: int = 0
    # URL del CSV de precios mayoristas ODEPA (frutas y hortalizas).
    # Dataset CKAN: precios-mayoristas-de-frutas-y-hortalizas
    # La URL apunta al año actual. ODEPA publica un CSV por año, así que
    # toca actualizar este valor anualmente (~enero de cada año).
    # Sobrescribible con ODEPA_CSV_URL en .env
    odepa_csv_url: str = (
        "https://datos.odepa.gob.cl/dataset/"
        "33f10516-acbe-4446-b633-68244b9b6b26/resource/"
        "580beca0-e87e-4dd4-9e8a-0bd92773f4a6/download/"
        "precio_mayorista_fruta-hortaliza_2026.csv"
    )
    # Fallback via CKAN API (mismo dataset, URL dinámica) (#175).
    odepa_fallback_urls: str = (
        "https://datos.odepa.gob.cl/api/3/action/datastore_search?"
        "resource_id=580beca0-e87e-4dd4-9e8a-0bd92773f4a6&limit=32000"
    )
    # Productos a sincronizar, separados por coma (lowercase).
    # "*" = sincronizar TODOS los productos del CSV (60+ productos ODEPA).
    odepa_productos: str = "*"

    # ── Modelos IA ───────────────────────
    whisper_model: str = "small"
    whisper_model_path: str = ""  # Directorio para modelos Whisper (vacio = default ~/.cache/whisper/)
    llm_model_path: str = "models/qwen2.5-3b-q4_k_m.gguf"
    piper_voice: str = "es_MX-claude-high"

    # ── Admin ────────────────────────────
    # API key para el dashboard admin. Default de dev — validar en prod.
    admin_api_key: str = "dev-admin-key"
    # Secreto para firmar cookies de sesión del admin (itsdangerous).
    # Default de dev — validar en prod.
    admin_session_secret: str = "agrovoz-dev-session-secret"
    # Tiempo de vida de la cookie de sesión admin (segundos). 8h por defecto.
    admin_session_ttl: int = 8 * 60 * 60

    # ── Modelos IA (paths) ───────────────
    piper_model_path: str = "models/es_MX-claude-high.onnx"

    # ── OpenRouter (fallback LLM remoto) ─
    # Vacio por defecto: el fallback esta DESHABILITADO hasta que se configure
    # una API key explicitamente. Se usa SOLO si el LLM local (Qwen2.5-3B) no
    # esta disponible o falla generando (Issue: fallback OpenRouter free tier).
    # "openrouter/free" es el router automatico de OpenRouter: elige entre los
    # modelos gratuitos disponibles que soporten tool calling. El catalogo
    # rota sin aviso (no es un modelo fijo) — ver docs/ARCHITECTURE.md.
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_timeout_seconds: float = 12.0

    # ── CORS ──────────────────────────────
    # Origenes permitidos para CORS en produccion.
    # Separados por coma. En desarrollo se usa "*" automaticamente.
    cors_origins: str = "https://agrovoz.cl,https://www.agrovoz.cl"

    @property
    def cors_origins_list(self) -> list[str]:
        """Retorna la lista de origenes CORS.

        En desarrollo retorna ["*"] para facilitar el desarrollo local.
        En produccion retorna la lista configurada en CORS_ORIGINS.
        """
        if self.app_env == "development":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ── Seguridad ────────────────────────
    rate_limit_per_minute: int = 60
    weather_rate_limit_per_minute: int = 30
    demo_rate_limit_per_minute: int = 5
    audio_retention_hours: int = 24
    phone_hash_pepper: str = "agrovoz-dev-pepper"  # Cambiar en producción (PHONE_HASH_PEPPER en .env)
    extra_allowed_hosts: str = ""  # Hosts/IPs extra separadas por coma para TrustedHostMiddleware

    # ── Demo web ─────────────────────────
    # Endpoint POST /api/v1/demo/preguntar para la landing page interactiva.
    # En producción debe estar deshabilitado (False) para evitar abuso del LLM/TTS.
    demo_endpoint_enabled: bool = False

    # ── Versión ──────────────────────────
    app_version: str = "0.1.0-dev"

    # ── Feature flags ────────────────────
    # Activar extracción tipada de variables (Pydantic) antes del tool calling.
    # Issue #191. Default false: usa keyword matching tradicional.
    use_typed_extraction: bool = False
    # Activar state machine de conversación multi-turno.
    # Issue #192. Default false: pipeline opera en modo stateless.
    use_conversation_state: bool = False

    # ── Logging ──────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @property
    def odepa_productos_list(self) -> list[str] | None:
        """Lista de productos ODEPA normalizada (lowercase, sin espacios).

        Retorna None cuando el valor es '*' (sincronizar todos los productos).
        Retorna lista vacía cuando el valor es '' o ',' (no sincronizar nada).
        """
        raw = self.odepa_productos.strip()
        if raw == "*":
            return None
        return [p.strip().lower() for p in raw.split(",") if p.strip()]

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
        # OpenMeteo no requiere API key (issue #51).
        # Mantenemos openweathermap_api_key como deprecated por compatibilidad.

    def validate_admin_keys_not_default(self) -> None:
        """Bloquea arranque en producción si admin_api_key o admin_session_secret son defaults públicos.

        En development/test, los defaults ('dev-admin-key', 'agrovoz-dev-session-secret')
        son aceptables para no bloquear el arranque local. En production, cualquier
        valor default o vacío es un riesgo: cualquiera que conozca el repo podría
        entrar al dashboard o falsificar cookies de sesión.
        """
        _default_key = "dev-admin-key"
        _default_secret = "agrovoz-dev-session-secret"

        for campo, valor, default in [
            ("ADMIN_API_KEY", self.admin_api_key, _default_key),
            ("ADMIN_SESSION_SECRET", self.admin_session_secret, _default_secret),
        ]:
            if not valor or valor == default:
                if self.app_env == "production":
                    raise ValueError(
                        f"{campo} es el valor default público o está vacío. "
                        f"Debe setear {campo} con un valor secreto "
                        "antes de desplegar a producción."
                    )
                if self.app_env != "development":
                    warnings.warn(
                        f"{campo} es el valor default público o está vacío. "
                        "Cámbielo antes de desplegar a producción.",
                        RuntimeWarning,
                        stacklevel=2,
                    )


settings = Settings()
settings.validate_webhook_secret_not_default()
settings.validate_pepper_not_default()
settings.validate_admin_keys_not_default()
settings.validate_api_keys_in_dev()
