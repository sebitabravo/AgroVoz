"""Puente ARI del canal IVR de respaldo: VAD, streaming y barge-in (C10, #172).

Traduce eventos de Asterisk ARI (StasisStart, ChannelTalkingStarted/Finished,
PlaybackFinished) a la máquina de estados de turno (IvrTurnController) y
traduce las acciones que esa máquina decide de vuelta a comandos REST de
ARI (Playback, Record, variable TALK_DETECT, Hangup).

Solo corre localmente contra el perfil ``ivr`` de Docker Compose — no hay
conexión a PSTN ni SIP público (ver docs/spike-ivr.md). El WebSocket de
eventos requiere la dependencia opcional ``websockets`` (grupo dev, ver
pyproject.toml): nunca se importa desde app/, así que no viaja en la imagen
de producción del backend.

Referencias verificadas de la API REST de Asterisk ARI (docs.asterisk.org,
julio 2026): POST /channels/{id}/answer, POST /channels/{id}/play,
DELETE /playbacks/{id}, POST /channels/{id}/record,
POST /recordings/live/{name}/stop, POST /channels/{id}/variable,
DELETE /channels/{id} (hangup), WebSocket GET /events?app=...&api_key=....
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.services.ivr_turn_service import IvrAction, IvrTurnController
from app.services.tts_service import TTSService
from app.services.whisper_service import WhisperService

logger = logging.getLogger(__name__)

_DEFAULT_APP_NAME = "agrovoz-ivr"
_TALK_DETECT_SILENCE_MS = 2500
_TALK_DETECT_ENERGY_THRESHOLD = 256


def build_ws_url(host: str, port: int, app_name: str, user: str, password: str) -> str:
    """Arma la URL del WebSocket de eventos ARI para una aplicación Stasis."""
    return f"ws://{host}:{port}/ari/events?app={app_name}&api_key={user}:{password}&subscribeAll=true"


def build_talk_detect_value() -> str:
    """Valor del channel variable TALK_DETECT: `<silencio_ms>,<umbral_energia>`."""
    return f"{_TALK_DETECT_SILENCE_MS},{_TALK_DETECT_ENERGY_THRESHOLD}"


class IvrAriClient:
    """Adaptador delgado sobre la API REST de ARI (sin lógica de negocio)."""

    def __init__(self, base_url: str, user: str, password: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, auth=(user, password), timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def answer(self, channel_id: str) -> None:
        await self._client.post(f"/channels/{channel_id}/answer")

    async def enable_talk_detect(self, channel_id: str) -> None:
        await self._client.post(
            f"/channels/{channel_id}/variable",
            params={"variable": "TALK_DETECT(set)", "value": build_talk_detect_value()},
        )

    async def play(self, channel_id: str, media: str) -> str:
        """Inicia la reproducción de un sonido y retorna el playback_id."""
        resp = await self._client.post(f"/channels/{channel_id}/play", params={"media": f"sound:{media}"})
        resp.raise_for_status()
        playback_id: str = resp.json()["id"]
        return playback_id

    async def stop_playback(self, playback_id: str) -> None:
        await self._client.delete(f"/playbacks/{playback_id}")

    async def start_recording(self, channel_id: str, recording_name: str) -> None:
        await self._client.post(
            f"/channels/{channel_id}/record",
            params={
                "name": recording_name,
                "format": "wav",
                "maxSilenceSeconds": 3,
                "beep": "false",
                "ifExists": "overwrite",
            },
        )

    async def stop_recording(self, recording_name: str) -> None:
        await self._client.post(f"/recordings/live/{recording_name}/stop")

    async def hangup(self, channel_id: str) -> None:
        await self._client.delete(f"/channels/{channel_id}")


@dataclass
class IvrCallSession:
    """Estado de una llamada: turno de habla + último playback/grabación activos.

    Traduce eventos ARI ya parseados (dict) a llamadas del ARI client,
    delegando en ``controller`` la decisión de qué acción corresponde.
    Separado de la conexión WebSocket real para poder testear el mapeo
    evento -> acción -> comando REST con un ``IvrAriClient`` mockeado.
    """

    channel_id: str
    controller: IvrTurnController = field(default_factory=IvrTurnController)
    active_playback_id: str | None = None
    recording_name: str = ""

    def _new_recording_name(self) -> str:
        self.recording_name = f"ivr-{self.channel_id}-{uuid.uuid4().hex[:8]}"
        return self.recording_name

    async def _apply_action(
        self,
        action: IvrAction,
        ari: IvrAriClient,
        *,
        chunks: Sequence[str] = (),
        sounds_prefix: str = "",
    ) -> None:
        """Ejecuta contra ARI la acción que decidió el controller."""
        if action == IvrAction.START_PLAYBACK:
            for i, _chunk_text in enumerate(chunks):
                media = f"{sounds_prefix}_{i:03d}"
                self.active_playback_id = await ari.play(self.channel_id, media)
        elif action == IvrAction.START_RECORDING:
            await ari.start_recording(self.channel_id, self._new_recording_name())
        elif action == IvrAction.STOP_PLAYBACK_START_RECORDING:
            if self.active_playback_id:
                await ari.stop_playback(self.active_playback_id)
                self.active_playback_id = None
            await ari.start_recording(self.channel_id, self._new_recording_name())
        elif action == IvrAction.STOP_RECORDING_TRANSCRIBE:
            await ari.stop_recording(self.recording_name)
        elif action == IvrAction.HANGUP:
            await ari.hangup(self.channel_id)

    async def handle_stasis_start(
        self,
        ari: IvrAriClient,
        *,
        greeting_chunks: Sequence[str],
        sounds_prefix: str,
    ) -> None:
        await ari.answer(self.channel_id)
        await ari.enable_talk_detect(self.channel_id)
        action = self.controller.start_playback(list(greeting_chunks))
        await self._apply_action(action, ari, chunks=greeting_chunks, sounds_prefix=sounds_prefix)

    async def handle_talking_started(self, ari: IvrAriClient) -> None:
        action = self.controller.on_talk_started()
        await self._apply_action(action, ari)

    async def handle_talking_finished(self, ari: IvrAriClient) -> None:
        action = self.controller.on_talk_finished()
        await self._apply_action(action, ari)

    async def handle_playback_finished(self, ari: IvrAriClient) -> None:
        self.active_playback_id = None
        action = self.controller.on_playback_finished()
        await self._apply_action(action, ari)

    async def handle_transcription_ready(
        self,
        ari: IvrAriClient,
        *,
        response_chunks: Sequence[str],
        sounds_prefix: str,
    ) -> None:
        action = self.controller.on_transcription_ready(list(response_chunks))
        await self._apply_action(action, ari, chunks=response_chunks, sounds_prefix=sounds_prefix)


async def _synthesize_chunks_to_sounds_dir(text: str, sounds_dir: Path, tag: str, tts: TTSService) -> list[str]:
    """Sintetiza `text` en fragmentos cortos y los publica en el volumen de Asterisk.

    Cada fragmento se convierte a WAV 8kHz mono (formato telefónico) y se
    nombra ``{tag}_NNN.wav``, listo para reproducirse con Playback usando
    ``sound:agrovoz/{tag}_NNN`` (sin extensión, convención de Asterisk).

    Returns:
        Lista de nombres base (sin extensión) en orden de reproducción.
    """
    import subprocess

    chunks = tts._split_text(text)
    if not chunks:
        return []

    names: list[str] = []
    for i, chunk_text in enumerate(chunks):
        name = f"{tag}_{i:03d}"
        ogg_path = Path(tts.synthesize(chunk_text, output_dir=sounds_dir))
        wav_path = sounds_dir / f"{name}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(ogg_path), "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        ogg_path.unlink(missing_ok=True)
        names.append(name)
    return names


async def _transcribe_recording(recording_path: Path, whisper: WhisperService) -> str:
    """Transcribe una grabación telefónica (8kHz) reutilizando WhisperService."""
    import subprocess

    resampled = recording_path.with_suffix(".16k.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(recording_path), "-ar", "16000", "-ac", "1", str(resampled)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    try:
        result = whisper.transcribe(resampled)
    finally:
        resampled.unlink(missing_ok=True)
    return str(result.get("text", "")).strip()


async def _generate_response_text(transcribed_text: str) -> str:
    """Genera la respuesta hablada a partir de lo transcrito.

    Reutiliza el mismo fallback determinista por keywords que usa el canal
    de WhatsApp (``_force_keyword_tool``) en vez de duplicar la detección de
    producto: mismo comportamiento, una sola fuente de verdad.
    """
    from app.services.llm_keywords import _force_keyword_tool

    response = await _force_keyword_tool(transcribed_text)
    return response or "No entendí el producto. ¿Podés repetirlo?"


class IvrAriBridge:
    """Orquesta la conexión WebSocket de eventos y despacha a cada IvrCallSession."""

    def __init__(
        self,
        *,
        ari: IvrAriClient,
        sounds_dir: Path,
        recordings_dir: Path,
        tts: TTSService | None = None,
        whisper: WhisperService | None = None,
    ) -> None:
        self._ari = ari
        self._sounds_dir = sounds_dir
        self._recordings_dir = recordings_dir
        self._tts = tts or TTSService()
        self._whisper = whisper or WhisperService()
        self._sessions: dict[str, IvrCallSession] = {}

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        channel = event.get("channel") or {}
        channel_id = channel.get("id", "")

        if event_type == "StasisStart":
            new_session = IvrCallSession(channel_id=channel_id)
            self._sessions[channel_id] = new_session
            tag = f"greeting-{channel_id}"
            names = await _synthesize_chunks_to_sounds_dir(
                "Bienvenido a AgroVoz. El precio de la papa es el siguiente.",
                self._sounds_dir,
                tag,
                self._tts,
            )
            await new_session.handle_stasis_start(
                self._ari,
                greeting_chunks=names,
                sounds_prefix=f"agrovoz/{tag}",
            )
            return

        session = self._sessions.get(channel_id)
        if session is None:
            return

        if event_type == "ChannelTalkingStarted":
            await session.handle_talking_started(self._ari)
        elif event_type == "ChannelTalkingFinished":
            await session.handle_talking_finished(self._ari)
            recording_path = self._recordings_dir / f"{session.recording_name}.wav"
            transcribed = await _transcribe_recording(recording_path, self._whisper)
            response_text = await _generate_response_text(transcribed)
            tag = f"reply-{channel_id}-{uuid.uuid4().hex[:6]}"
            names = await _synthesize_chunks_to_sounds_dir(response_text, self._sounds_dir, tag, self._tts)
            await session.handle_transcription_ready(
                self._ari,
                response_chunks=names,
                sounds_prefix=f"agrovoz/{tag}",
            )
        elif event_type == "PlaybackFinished":
            await session.handle_playback_finished(self._ari)
        elif event_type == "StasisEnd":
            # El canal ya salió de Stasis (colgó o fue transferido): no hay
            # nada que colgar, solo cerrar el turno y soltar la sesión local.
            session.controller.hangup()
            self._sessions.pop(channel_id, None)

    async def run(self, ws_url: str) -> None:
        """Conecta al WebSocket de eventos ARI y despacha cada mensaje."""
        import websockets

        async with websockets.connect(ws_url) as ws:
            logger.info("Conectado al WebSocket de eventos ARI")
            async for raw_message in ws:
                try:
                    event = json.loads(raw_message)
                except json.JSONDecodeError:
                    logger.warning("Evento ARI no parseable, se descarta")
                    continue
                await self._handle_event(event)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Puente ARI del spike IVR (VAD, streaming, barge-in).")
    parser.add_argument("--host", default="ivr")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--app-name", default=_DEFAULT_APP_NAME)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--sounds-dir", type=Path, default=Path("/app/data/ivr"))
    parser.add_argument("--recordings-dir", type=Path, default=Path("/app/data/ivr/recordings"))
    return parser


async def _main_async(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    base_url = f"http://{args.host}:{args.port}/ari"
    ari = IvrAriClient(base_url, args.user, args.password)
    bridge = IvrAriBridge(ari=ari, sounds_dir=args.sounds_dir, recordings_dir=args.recordings_dir)
    ws_url = build_ws_url(args.host, args.port, args.app_name, args.user, args.password)
    try:
        await bridge.run(ws_url)
    finally:
        await ari.aclose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
