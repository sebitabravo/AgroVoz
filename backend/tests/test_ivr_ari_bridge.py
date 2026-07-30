"""Tests del puente ARI del spike IVR (C10): mapeo evento -> acción -> comando REST.

No requiere un Asterisk real: IvrAriClient se mockea por completo. Lo que se
prueba es que cada evento de Asterisk dispare la llamada REST correcta según
lo que decide IvrTurnController — la integración real con un ARI vivo queda
documentada como verificación manual en docs/spike-ivr.md.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.services.ivr_turn_service import IvrTurnState
from scripts.ivr_ari_bridge import (
    IvrAriBridge,
    IvrAriClient,
    IvrCallSession,
    _generate_response_text,
    _synthesize_chunks_to_sounds_dir,
    _transcribe_recording,
    build_talk_detect_value,
    build_ws_url,
)


def test_build_ws_url_incluye_app_y_credenciales() -> None:
    url = build_ws_url("ivr", 8088, "agrovoz-ivr", "agrovoz", "secreto")

    assert url == "ws://ivr:8088/ari/events?app=agrovoz-ivr&api_key=agrovoz:secreto&subscribeAll=true"


def test_build_talk_detect_value_formato_silencio_coma_energia() -> None:
    assert build_talk_detect_value() == "2500,256"


@pytest.fixture
def ari() -> AsyncMock:
    return AsyncMock()


class TestIvrCallSessionStasisStart:
    """La llegada de un canal nuevo contesta, habilita VAD y reproduce el saludo."""

    async def test_contesta_habilita_talk_detect_y_reproduce(self, ari: AsyncMock) -> None:
        ari.play.return_value = "playback-1"
        session = IvrCallSession(channel_id="chan-1")

        await session.handle_stasis_start(ari, greeting_chunks=["saludo_000"], sounds_prefix="agrovoz/saludo")

        ari.answer.assert_awaited_once_with("chan-1")
        ari.enable_talk_detect.assert_awaited_once_with("chan-1")
        ari.play.assert_awaited_once_with("chan-1", "agrovoz/saludo_000")
        assert session.active_playback_id == "playback-1"
        assert session.controller.state == IvrTurnState.PLAYING


class TestIvrCallSessionBargeIn:
    """Hablar durante la locución corta el playback activo y empieza a grabar."""

    async def test_talking_started_corta_playback_y_graba(self, ari: AsyncMock) -> None:
        ari.play.return_value = "playback-1"
        session = IvrCallSession(channel_id="chan-1")
        await session.handle_stasis_start(ari, greeting_chunks=["saludo_000"], sounds_prefix="agrovoz/saludo")

        await session.handle_talking_started(ari)

        ari.stop_playback.assert_awaited_once_with("playback-1")
        ari.start_recording.assert_awaited_once()
        assert session.active_playback_id is None
        assert session.controller.state == IvrTurnState.LISTENING

    async def test_talking_started_sin_playback_previo_no_corta_nada(self, ari: AsyncMock) -> None:
        session = IvrCallSession(channel_id="chan-1")

        await session.handle_talking_started(ari)

        ari.stop_playback.assert_not_awaited()
        ari.start_recording.assert_not_awaited()


class TestIvrCallSessionTalkFinished:
    """Dejar de hablar corta la grabación para pasar a transcribir."""

    async def test_talking_finished_detiene_grabacion(self, ari: AsyncMock) -> None:
        session = IvrCallSession(channel_id="chan-1", recording_name="")
        session.controller.state = IvrTurnState.LISTENING
        session.recording_name = "grabacion-1"

        await session.handle_talking_finished(ari)

        ari.stop_recording.assert_awaited_once_with("grabacion-1")


class TestIvrCallSessionPlaybackFinished:
    """La locución termina sola: pasa a grabar la respuesta del productor."""

    async def test_playback_finished_empieza_a_grabar(self, ari: AsyncMock) -> None:
        ari.play.return_value = "playback-1"
        session = IvrCallSession(channel_id="chan-1")
        await session.handle_stasis_start(ari, greeting_chunks=["saludo_000"], sounds_prefix="agrovoz/saludo")

        await session.handle_playback_finished(ari)

        ari.start_recording.assert_awaited_once()
        assert session.active_playback_id is None


class TestIvrCallSessionTranscriptionReady:
    """Con la respuesta lista, reproduce los nuevos fragmentos."""

    async def test_transcription_ready_reproduce_respuesta(self, ari: AsyncMock) -> None:
        ari.play.return_value = "playback-2"
        session = IvrCallSession(channel_id="chan-1")
        session.controller.state = IvrTurnState.PROCESSING

        await session.handle_transcription_ready(
            ari,
            response_chunks=["reply_000"],
            sounds_prefix="agrovoz/reply",
        )

        ari.play.assert_awaited_once_with("chan-1", "agrovoz/reply_000")
        assert session.controller.state == IvrTurnState.PLAYING

    async def test_transcription_vacia_no_reproduce_nada(self, ari: AsyncMock) -> None:
        session = IvrCallSession(channel_id="chan-1")
        session.controller.state = IvrTurnState.PROCESSING

        await session.handle_transcription_ready(ari, response_chunks=[], sounds_prefix="agrovoz/reply")

        ari.play.assert_not_awaited()


class TestIvrAriBridgeHandleEvent:
    """El despachador de la conexión completa: StasisStart/End y evento de canal desconocido."""

    def _make_bridge(self, tmp_path: Path) -> tuple[IvrAriBridge, AsyncMock]:
        ari = AsyncMock()
        ari.play.return_value = "playback-1"
        tts = Mock()
        tts._split_text = lambda _text: ["frase"]
        tts.synthesize.return_value = str(tmp_path / "fake.ogg")
        whisper = Mock()
        bridge = IvrAriBridge(
            ari=ari,
            sounds_dir=tmp_path,
            recordings_dir=tmp_path,
            tts=tts,
            whisper=whisper,
        )
        return bridge, ari

    async def test_evento_de_canal_no_registrado_se_ignora(self, tmp_path: Path) -> None:
        bridge, ari = self._make_bridge(tmp_path)

        await bridge._handle_event({"type": "ChannelTalkingStarted", "channel": {"id": "chan-inexistente"}})

        ari.stop_playback.assert_not_awaited()

    async def test_stasis_start_crea_sesion_y_reproduce_saludo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge, ari = self._make_bridge(tmp_path)
        (tmp_path / "fake.ogg").write_bytes(b"ogg")

        def fake_run(command: list[str], **_kwargs: object) -> None:
            Path(command[-1]).write_bytes(b"wav")

        import subprocess

        monkeypatch.setattr(subprocess, "run", fake_run)

        await bridge._handle_event({"type": "StasisStart", "channel": {"id": "chan-1"}})

        ari.answer.assert_awaited_once_with("chan-1")
        assert "chan-1" in bridge._sessions

    async def test_stasis_end_cierra_la_sesion(self, tmp_path: Path) -> None:
        bridge, _ari = self._make_bridge(tmp_path)
        bridge._sessions["chan-1"] = IvrCallSession(channel_id="chan-1")

        await bridge._handle_event({"type": "StasisEnd", "channel": {"id": "chan-1"}})

        assert "chan-1" not in bridge._sessions

    async def test_playback_finished_delega_en_la_sesion(self, tmp_path: Path) -> None:
        bridge, ari = self._make_bridge(tmp_path)
        session = IvrCallSession(channel_id="chan-1")
        session.controller.start_playback(["texto"])
        session.active_playback_id = "playback-1"
        bridge._sessions["chan-1"] = session

        await bridge._handle_event({"type": "PlaybackFinished", "channel": {"id": "chan-1"}})

        ari.start_recording.assert_awaited_once()
        assert session.active_playback_id is None

    async def test_channel_talking_finished_transcribe_y_reproduce_respuesta(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge, ari = self._make_bridge(tmp_path)
        session = IvrCallSession(channel_id="chan-1")
        session.controller.state = IvrTurnState.LISTENING
        session.recording_name = "grabacion-1"
        bridge._sessions["chan-1"] = session

        monkeypatch.setattr(
            "scripts.ivr_ari_bridge._transcribe_recording",
            AsyncMock(return_value="cuanto esta el trigo"),
        )
        monkeypatch.setattr(
            "scripts.ivr_ari_bridge._generate_response_text",
            AsyncMock(return_value="El trigo está a 300 pesos el kilo."),
        )
        monkeypatch.setattr(
            "scripts.ivr_ari_bridge._synthesize_chunks_to_sounds_dir",
            AsyncMock(return_value=["reply_000"]),
        )

        await bridge._handle_event({"type": "ChannelTalkingFinished", "channel": {"id": "chan-1"}})

        ari.stop_recording.assert_awaited_once_with("grabacion-1")
        ari.play.assert_awaited_once()
        assert session.controller.state == IvrTurnState.PLAYING


class TestApplyActionHangup:
    """El mapeo de IvrAction.HANGUP -> ari.hangup, aunque el bridge todavía no lo dispara.

    Ningún evento de Asterisk conectado hoy dispara HANGUP (StasisEnd solo
    limpia el estado local: el canal ya se fue, no hay nada que colgar). Se
    deja probado igual porque es parte del contrato público de la máquina de
    estados y un futuro trigger de negocio (ej: demasiados reintentos) podría
    usarlo sin tener que descubrir en producción que el mapeo nunca se probó.
    """

    async def test_hangup_llama_a_ari_hangup(self, ari: AsyncMock) -> None:
        session = IvrCallSession(channel_id="chan-1")
        session.controller.start_playback(["texto"])

        action = session.controller.hangup()
        await session._apply_action(action, ari)

        ari.hangup.assert_awaited_once_with("chan-1")


def _mock_transport(
    expected_method: str,
    expected_path: str,
    json_body: dict[str, object] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == expected_method
        assert request.url.path == expected_path
        return httpx.Response(200, json=json_body or {})

    return httpx.MockTransport(handler)


class TestIvrAriClientRest:
    """Cada método del cliente ARI arma la ruta y el verbo HTTP correctos."""

    async def _client_with_transport(self, transport: httpx.MockTransport) -> IvrAriClient:
        client = IvrAriClient("http://ivr:8088/ari", "agrovoz", "secreto")
        client._client = httpx.AsyncClient(transport=transport, base_url="http://ivr:8088/ari")
        return client

    async def test_answer(self) -> None:
        client = await self._client_with_transport(_mock_transport("POST", "/ari/channels/chan-1/answer"))
        try:
            await client.answer("chan-1")
        finally:
            await client.aclose()

    async def test_enable_talk_detect(self) -> None:
        client = await self._client_with_transport(_mock_transport("POST", "/ari/channels/chan-1/variable"))
        try:
            await client.enable_talk_detect("chan-1")
        finally:
            await client.aclose()

    async def test_play_retorna_playback_id(self) -> None:
        client = await self._client_with_transport(
            _mock_transport("POST", "/ari/channels/chan-1/play", {"id": "playback-9"})
        )
        try:
            playback_id = await client.play("chan-1", "agrovoz/saludo_000")
        finally:
            await client.aclose()
        assert playback_id == "playback-9"

    async def test_stop_playback(self) -> None:
        client = await self._client_with_transport(_mock_transport("DELETE", "/ari/playbacks/playback-9"))
        try:
            await client.stop_playback("playback-9")
        finally:
            await client.aclose()

    async def test_start_recording(self) -> None:
        client = await self._client_with_transport(_mock_transport("POST", "/ari/channels/chan-1/record"))
        try:
            await client.start_recording("chan-1", "grabacion-1")
        finally:
            await client.aclose()

    async def test_stop_recording(self) -> None:
        client = await self._client_with_transport(_mock_transport("POST", "/ari/recordings/live/grabacion-1/stop"))
        try:
            await client.stop_recording("grabacion-1")
        finally:
            await client.aclose()

    async def test_hangup(self) -> None:
        client = await self._client_with_transport(_mock_transport("DELETE", "/ari/channels/chan-1"))
        try:
            await client.hangup("chan-1")
        finally:
            await client.aclose()


class TestSynthesizeChunksToSoundsDir:
    """Publica cada fragmento como WAV 8kHz mono en el volumen compartido con Asterisk."""

    async def test_texto_vacio_no_genera_nada(self, tmp_path: Path) -> None:
        tts = Mock()
        tts._split_text = lambda _text: []

        names = await _synthesize_chunks_to_sounds_dir("", tmp_path, "tag", tts)

        assert names == []
        tts.synthesize.assert_not_called()

    async def test_cada_fragmento_se_convierte_y_se_nombra_en_orden(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tts = Mock()
        tts._split_text = lambda _text: ["Primera frase", "Segunda frase"]
        ogg_path = tmp_path / "source.ogg"
        ogg_path.write_bytes(b"ogg")
        tts.synthesize.return_value = str(ogg_path)

        run_calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> None:
            run_calls.append(command)
            Path(command[-1]).write_bytes(b"wav")

        import subprocess

        monkeypatch.setattr(subprocess, "run", fake_run)

        names = await _synthesize_chunks_to_sounds_dir("Primera frase. Segunda frase.", tmp_path, "tag", tts)

        assert names == ["tag_000", "tag_001"]
        assert len(run_calls) == 2
        assert (tmp_path / "tag_000.wav").exists()
        assert (tmp_path / "tag_001.wav").exists()
        assert not ogg_path.exists()


class TestTranscribeRecording:
    """Resamplea a 16kHz antes de pasarle la grabación a Whisper."""

    async def test_transcribe_resamplea_y_limpia_el_temporal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recording = tmp_path / "grabacion-1.wav"
        recording.write_bytes(b"wav-8khz")
        whisper = Mock()
        whisper.transcribe.return_value = {"text": "  cuanto esta el trigo  "}

        def fake_run(command: list[str], **_kwargs: object) -> None:
            Path(command[-1]).write_bytes(b"wav-16khz")

        import subprocess

        monkeypatch.setattr(subprocess, "run", fake_run)

        text = await _transcribe_recording(recording, whisper)

        assert text == "cuanto esta el trigo"
        assert not (tmp_path / "grabacion-1.16k.wav").exists()


class TestGenerateResponseText:
    """Reutiliza el fallback por keywords del canal de WhatsApp."""

    async def test_con_keyword_reconocida_retorna_la_respuesta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_force_keyword_tool(_text: str, phone_hash: str | None = None) -> str:
            return "El trigo está a 300 pesos el kilo, según ODEPA."

        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_keyword_tool,
        )

        response = await _generate_response_text("cuanto esta el trigo")

        assert "300 pesos" in response

    async def test_sin_keyword_reconocida_pide_repetir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_force_keyword_tool(_text: str, phone_hash: str | None = None) -> None:
            return None

        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_keyword_tool,
        )

        response = await _generate_response_text("asdasdasd")

        assert "no entendí" in response.lower()
