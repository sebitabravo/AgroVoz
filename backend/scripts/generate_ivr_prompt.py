"""Genera el audio estático que reproduce el flujo IVR local (#172)."""

from __future__ import annotations

import argparse
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

from app.core.database import SessionLocal
from app.services.odepa_service import get_price_for_llm
from app.services.tts_service import TTSService

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT = Path("/app/data/ivr/precio-papa.wav")


def generate_ivr_prompt(
    *,
    producto: str = "papa",
    mercado: str = "",
    output_path: Path = _DEFAULT_OUTPUT,
) -> Path:
    """Consulta ODEPA, sintetiza con Piper y publica un WAV 8 kHz atómico."""
    session = SessionLocal()
    try:
        response_text = get_price_for_llm(session, producto, mercado)
    finally:
        session.close()

    if response_text.startswith(("No tengo", "No entendí")):
        raise RuntimeError("ODEPA no entregó un precio utilizable para el IVR")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_wav = output_path.with_suffix(".tmp.wav")
    generated_ogg: Path | None = None
    try:
        generated_ogg = Path(
            TTSService().synthesize(
                response_text,
                output_dir=output_path.parent,
            )
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(generated_ogg),
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(temporary_wav),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        temporary_wav.replace(output_path)
    finally:
        temporary_wav.unlink(missing_ok=True)
        if generated_ogg is not None:
            generated_ogg.unlink(missing_ok=True)

    logger.info(
        "Prompt IVR publicado — producto=%s bytes=%d",
        producto.strip().lower(),
        output_path.stat().st_size,
    )
    return output_path


def _build_parser() -> argparse.ArgumentParser:
    """Construye argumentos del generador one-shot."""
    parser = argparse.ArgumentParser(description="Genera un prompt ODEPA/Piper para el IVR local.")
    parser.add_argument("--producto", default="papa")
    parser.add_argument("--mercado", default="")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta el generador y retorna un código apto para cron/smoke."""
    args = _build_parser().parse_args(argv)
    try:
        generate_ivr_prompt(
            producto=args.producto,
            mercado=args.mercado,
            output_path=args.output,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        logger.error("No se pudo generar el prompt IVR")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
