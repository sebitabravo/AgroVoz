"""Siembra datos demo realistas en la tabla consultations.

El panel admin se demuestra ante el jurado de Crea INACAP con datos reales
del pipeline, pero en desarrollo la DB se llena de transcripciones de prueba
y alucinaciones de Whisper (ej: "Subtítulos por la comunidad de Amara.org")
que dejan el dashboard con tasa de éxito 0% y latencias absurdas.

Este script BORRA todas las consultas existentes y siembra ~14 días de
actividad creíble: 6 agricultores anónimos, intents ponderados (precio/clima
mayoritarios, pocos desconocidos), consultas en español rural chileno y
latencias por etapa bajo el límite de 15s end-to-end.

Uso:
    uv run python scripts/seed_demo_data.py
    docker compose exec backend python scripts/seed_demo_data.py
"""

from __future__ import annotations

import datetime
import logging
import random
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.phone_hash import hash_phone
from app.models.consultation import Consultation

logger = logging.getLogger(__name__)

# Semilla fija → screenshots y demos reproducibles entre corridas.
_RNG = random.Random(42)

# Ventana de actividad: últimos 28 días. El dashboard compara los últimos 14
# días contra los 14 previos, así que sembramos ambas mitades para que la
# tendencia de 14 días muestre crecimiento creíble (no un +100% por vacío).
_DAYS = 28

# Números E.164 ficticios de 6 agricultores del piloto Traiguén. Se hashean
# con la pepper real para que el dashboard cuente "agricultores únicos" bien.
_PHONES = [
    "+56961112201",
    "+56961112202",
    "+56961112203",
    "+56961112204",
    "+56961112205",
    "+56961112206",
]

# Consultas reales esperadas del piloto, en español rural chileno.
_QUERIES: dict[str, list[str]] = {
    "precio": [
        "¿A cuánto está el kilo de papa?",
        "Compadre, ¿en cuánto anda la papa esta semana?",
        "Oiga, ¿a cómo está la papa en el mercado?",
        "¿Cuánto vale el saco de papas hoy día?",
        "Quiero saber el precio de la papa en Temuco",
        "¿Subió el precio de la papa o sigue igual?",
        "Dígame el precio mayorista de la papa por favor",
    ],
    "clima": [
        "¿Cómo viene el clima para mañana?",
        "¿Va a llover esta semana en Traiguén?",
        "¿Hay riesgo de helada esta noche?",
        "¿Qué tiempo va a hacer el fin de semana?",
        "Dígame el pronóstico para los próximos días",
    ],
    "desconocido": [
        "Hola, buenos días, ¿me puede ayudar?",
        "Gracias por la información, compadre",
        "¿Usted sabe algo de los subsidios del INDAP?",
    ],
}

# Respuestas tipo del asistente (datos plausibles, no agronómicos).
_RESPONSES: dict[str, list[str]] = {
    "precio": [
        "El precio mayorista de la papa en Temuco es de $480 por kilo según "
        "ODEPA, actualizado hoy.",
        "Según ODEPA, la papa está a $510 el kilo en el mercado mayorista de "
        "Temuco.",
        "El kilo de papa se cotiza en $465 esta semana según los datos de ODEPA.",
    ],
    "clima": [
        "En Traiguén se esperan 14 grados con cielo nublado y 60% de "
        "probabilidad de lluvia para mañana.",
        "El pronóstico para Traiguén marca 9 grados de mínima con riesgo de "
        "helada durante la madrugada.",
        "Para el fin de semana se esperan 18 grados y cielo despejado en "
        "Traiguén.",
    ],
    "desconocido": [
        "Disculpe, no entendí bien su consulta. Puede preguntarme por el "
        "precio de la papa o el clima de Traiguén.",
    ],
}

# Distribución de intents que refleja el uso esperado del piloto.
_INTENT_WEIGHTS = {"precio": 0.55, "clima": 0.32, "desconocido": 0.13}

# Rangos de latencia por etapa (ms). El total queda muy bajo el límite de 15s.
_STAGE_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "precio": {"whisper": (1200, 2400), "llm": (800, 1700), "tts": (1100, 2100)},
    "clima": {"whisper": (1300, 2500), "llm": (1500, 2900), "tts": (1100, 2200)},
    "desconocido": {"whisper": (1200, 2600), "llm": (600, 1200), "tts": (900, 1700)},
}


def _pick_intent() -> str:
    """Elige un intent según los pesos del piloto."""
    intents = list(_INTENT_WEIGHTS.keys())
    weights = list(_INTENT_WEIGHTS.values())
    return _RNG.choices(intents, weights=weights, k=1)[0]


def _build_consultation(day_offset: int) -> Consultation:
    """Construye una consulta ficticia para un día dado (offset desde hoy)."""
    intent = _pick_intent()
    stages = _STAGE_RANGES[intent]
    whisper_ms = _RNG.randint(*stages["whisper"])
    llm_ms = _RNG.randint(*stages["llm"])
    tts_ms = _RNG.randint(*stages["tts"])
    # Overhead de I/O (descarga audio, ffmpeg, envío) entre etapas.
    overhead = _RNG.randint(300, 900)
    latency_ms = whisper_ms + llm_ms + tts_ms + overhead

    # Hora hábil rural: entre las 08:00 y las 19:00.
    base = datetime.datetime.now() - datetime.timedelta(days=day_offset)
    created_at = base.replace(
        hour=_RNG.randint(8, 19),
        minute=_RNG.randint(0, 59),
        second=_RNG.randint(0, 59),
        microsecond=0,
    )

    return Consultation(
        phone_hash=hash_phone(_RNG.choice(_PHONES), settings.phone_hash_pepper),
        intent=intent,
        query_text=_RNG.choice(_QUERIES[intent]),
        response_text=_RNG.choice(_RESPONSES[intent]),
        audio_duration_ms=_RNG.randint(2500, 8500),
        latency_ms=latency_ms,
        whisper_ms=whisper_ms,
        llm_ms=llm_ms,
        tts_ms=tts_ms,
        created_at=created_at,
    )


def seed_demo_data() -> int:
    """Borra las consultas existentes y siembra actividad demo de 14 días.

    Returns:
        Cantidad de consultas insertadas.

    Raises:
        SQLAlchemyError: Si la base de datos responde con error.
    """
    db = SessionLocal()
    try:
        borradas = db.query(Consultation).delete()
        logger.info("Consultas previas borradas: %d", borradas)

        consultas: list[Consultation] = []
        for day_offset in range(_DAYS):
            # Actividad decreciente hacia atrás: la última semana es la más
            # activa, los 14 días previos quedan más livianos para que la
            # tendencia de 14 días refleje adopción creciente del piloto.
            if day_offset < 7:
                cantidad = _RNG.randint(6, 14)
            elif day_offset < 14:
                cantidad = _RNG.randint(4, 9)
            else:
                cantidad = _RNG.randint(2, 6)
            consultas.extend(_build_consultation(day_offset) for _ in range(cantidad))

        db.add_all(consultas)
        db.commit()
        logger.info("Consultas demo sembradas: %d", len(consultas))
        return len(consultas)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        total = seed_demo_data()
        logger.info("Listo. %d consultas demo en la base de datos.", total)
    except SQLAlchemyError as exc:
        logger.error("Error sembrando datos demo: %s", exc)
        sys.exit(1)
