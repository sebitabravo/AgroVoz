"""Integración del estado de entrega en los caminos de texto y audio."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.core.constants import Intent
from app.schemas.pipeline import AudioResponse
from app.services import audio_service, pipeline_service
from app.services.indap_credit_service import build_indap_sources_message
from app.services.report_service import REPORT_CAPTION, REPORT_FILENAME


def _response(
    *,
    consultation_id: int | None = 42,
    audio_path: str = "",
    text_response: str = "Respuesta principal",
    welcome_audio_path: str | None = None,
    es_primer_contacto: bool = False,
    intent: Intent = "precio",
    report_pdf_path: str | None = None,
) -> AudioResponse:
    return AudioResponse(
        audio_path=audio_path,
        text_response=text_response,
        latency_ms=10,
        intent=intent,
        consultation_id=consultation_id,
        welcome_audio_path=welcome_audio_path,
        es_primer_contacto=es_primer_contacto,
        report_pdf_path=report_pdf_path,
    )


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    response: AudioResponse,
) -> AsyncMock:
    process = AsyncMock(return_value=response)
    pipeline = Mock()
    pipeline.process = process
    monkeypatch.setattr(
        pipeline_service,
        "AgroVozPipeline",
        Mock(return_value=pipeline),
    )
    return process


def _install_openwa(
    monkeypatch: pytest.MonkeyPatch,
) -> Mock:
    openwa = Mock()
    openwa.send_audio = AsyncMock(return_value={})
    openwa.send_file = AsyncMock(return_value={})
    openwa.send_text = AsyncMock(return_value={})
    openwa.send_typing_indicator = AsyncMock(return_value={})
    monkeypatch.setattr(
        audio_service,
        "OpenWAService",
        Mock(return_value=openwa),
    )
    return openwa


def _install_delivery_spies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Mock, Mock, Mock]:
    delivered = Mock(return_value=True)
    failed = Mock(return_value=True)
    history = Mock(return_value="saved")
    monkeypatch.setattr(
        audio_service.delivery_service,
        "mark_delivery_delivered",
        delivered,
    )
    monkeypatch.setattr(
        audio_service.delivery_service,
        "mark_delivery_failed",
        failed,
    )
    monkeypatch.setattr(
        audio_service.consultation_history_service,
        "save_delivered_consultation_to_history",
        history,
    )
    monkeypatch.setattr(
        audio_service.delivery_service,
        "redact_consultation_content",
        Mock(return_value=True),
    )
    return delivered, failed, history


def _install_audio_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_convert(_input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"wav")

    monkeypatch.setattr(audio_service, "convert_ogg_to_wav", fake_convert)
    monkeypatch.setattr(audio_service, "get_audio_duration_ms", Mock(return_value=500))


@pytest.mark.asyncio
async def test_texto_exitoso_marca_consulta_entregada(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    _install_pipeline(monkeypatch, _response(consultation_id=101))

    await audio_service.AudioService(tmp_path).process_text(
        "precio de la papa",
        "56911111111@c.us",
        "req-texto-ok",
    )

    openwa.send_text.assert_awaited_once_with(
        "56911111111@c.us",
        "Respuesta principal",
    )
    delivered.assert_called_once_with(101)
    failed.assert_not_called()
    history.assert_called_once_with(101)


@pytest.mark.asyncio
async def test_texto_reporte_envia_pdf_y_borra_temporal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El PDF llega antes de la confirmación y el temporal se elimina."""
    openwa = _install_openwa(monkeypatch)
    send_order: list[str] = []
    openwa.send_file.side_effect = lambda *_args: send_order.append("pdf")
    openwa.send_text.side_effect = lambda *_args: send_order.append("text")
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    report_path = tmp_path / "reporte_semanal.pdf"
    report_path.write_bytes(b"%PDF-1.7 reporte determinista")
    _install_pipeline(
        monkeypatch,
        _response(consultation_id=103, intent="resumen", report_pdf_path=str(report_path)),
    )

    await audio_service.AudioService(tmp_path).process_text(
        "envíame el reporte semanal en PDF",
        "56911111111@c.us",
        "req-reporte-pdf",
    )

    openwa.send_text.assert_awaited_once_with(
        "56911111111@c.us",
        "Respuesta principal",
    )
    openwa.send_file.assert_awaited_once_with(
        "56911111111@c.us",
        str(report_path),
        REPORT_FILENAME,
        REPORT_CAPTION,
    )
    delivered.assert_called_once_with(103)
    failed.assert_not_called()
    history.assert_called_once_with(103)
    assert send_order == ["pdf", "text"]
    assert not report_path.exists()


@pytest.mark.asyncio
async def test_audio_reporte_envia_pdf_despues_del_audio_y_borra_temporales(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El canal de voz adjunta primero y limpia todos los temporales."""
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    send_order: list[str] = []
    openwa.send_file.side_effect = lambda *_args: send_order.append("pdf")
    openwa.send_audio.side_effect = lambda *_args: send_order.append("audio")
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    audio_path = tmp_path / "respuesta.ogg"
    audio_path.write_bytes(b"respuesta")
    report_path = tmp_path / "reporte_semanal.pdf"
    report_path.write_bytes(b"%PDF-1.7 reporte determinista")
    _install_pipeline(
        monkeypatch,
        _response(
            consultation_id=104,
            audio_path=str(audio_path),
            intent="resumen",
            report_pdf_path=str(report_path),
        ),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-audio-reporte-pdf",
    )

    openwa.send_audio.assert_awaited_once_with("56911111111@c.us", str(audio_path))
    openwa.send_file.assert_awaited_once_with(
        "56911111111@c.us",
        str(report_path),
        REPORT_FILENAME,
        REPORT_CAPTION,
    )
    delivered.assert_called_once_with(104)
    failed.assert_not_called()
    history.assert_called_once_with(104)
    assert send_order == ["pdf", "audio"]
    assert not audio_path.exists()
    assert not report_path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_texto_reporte_fallido_no_confirma_entrega(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Un send_file fallido marca error y reemplaza la confirmación."""
    openwa = _install_openwa(monkeypatch)
    openwa.send_file.side_effect = httpx.ReadTimeout("dato privado")
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    report_path = tmp_path / "reporte-fallido.pdf"
    report_path.write_bytes(b"%PDF-test")
    _install_pipeline(
        monkeypatch,
        _response(consultation_id=105, intent="resumen", report_pdf_path=str(report_path)),
    )

    await audio_service.AudioService(tmp_path).process_text(
        "mándame el reporte semanal",
        "56911111111@c.us",
        "req-reporte-fallido",
    )

    openwa.send_text.assert_awaited_once_with(
        "56911111111@c.us",
        "No pude adjuntar el reporte ahora. ¿Probamos de nuevo más tarde?",
    )
    delivered.assert_not_called()
    failed.assert_called_once_with(105, "openwa_send_failed")
    history.assert_not_called()
    assert not report_path.exists()


@pytest.mark.asyncio
async def test_audio_reporte_fallido_no_envia_confirmacion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si falla el adjunto, no se envía el audio que afirma que quedó listo."""
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    openwa.send_file.side_effect = httpx.HTTPError("dato privado")
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    audio_path = tmp_path / "confirmacion.ogg"
    audio_path.write_bytes(b"respuesta")
    report_path = tmp_path / "reporte-fallido.pdf"
    report_path.write_bytes(b"%PDF-test")
    _install_pipeline(
        monkeypatch,
        _response(
            consultation_id=106,
            audio_path=str(audio_path),
            intent="resumen",
            report_pdf_path=str(report_path),
        ),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-audio-reporte-fallido",
    )

    openwa.send_audio.assert_not_awaited()
    openwa.send_text.assert_awaited_once()
    delivered.assert_not_called()
    failed.assert_called_once_with(106, "openwa_send_failed")
    history.assert_not_called()
    assert not audio_path.exists()
    assert not report_path.exists()


@pytest.mark.asyncio
async def test_historial_fallido_igual_redacta_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una falla secundaria de historial no conserva contenido indefinidamente."""
    history = Mock(side_effect=RuntimeError("contenido privado"))
    redact = Mock(return_value=True)
    monkeypatch.setattr(
        audio_service.consultation_history_service,
        "save_delivered_consultation_to_history",
        history,
    )
    monkeypatch.setattr(
        audio_service.delivery_service,
        "redact_consultation_content",
        redact,
    )

    await audio_service._save_delivered_history(777)

    history.assert_called_once_with(777)
    redact.assert_called_once_with(777)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("send_error", "expected_code"),
    [
        (httpx.ReadTimeout("dato privado"), "openwa_timeout"),
        (httpx.HTTPError("dato privado"), "openwa_http_error"),
        (ValueError("dato privado"), "openwa_invalid_response"),
        (OSError("dato privado"), "openwa_send_failed"),
        (RuntimeError("dato privado"), "openwa_send_failed"),
    ],
)
async def test_texto_fallido_guarda_codigo_estable_sin_propagar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    send_error: Exception,
    expected_code: str,
) -> None:
    openwa = _install_openwa(monkeypatch)
    openwa.send_text.side_effect = send_error
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    _install_pipeline(monkeypatch, _response(consultation_id=102))

    await audio_service.AudioService(tmp_path).process_text(
        "precio de la papa",
        "56911111111@c.us",
        "req-texto-error",
    )

    delivered.assert_not_called()
    failed.assert_called_once_with(102, expected_code)
    history.assert_not_called()
    assert "dato privado" not in repr(failed.call_args)


@pytest.mark.asyncio
async def test_audio_exitoso_marca_consulta_entregada_y_limpia_archivos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    response_path = tmp_path / "respuesta.ogg"
    response_path.write_bytes(b"respuesta")
    _install_pipeline(
        monkeypatch,
        _response(consultation_id=201, audio_path=str(response_path)),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-audio-ok",
    )

    openwa.send_audio.assert_awaited_once_with(
        "56911111111@c.us",
        str(response_path),
    )
    delivered.assert_called_once_with(201)
    failed.assert_not_called()
    history.assert_called_once_with(201)
    openwa.send_text.assert_not_awaited()
    assert not response_path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_audio_fallido_marca_conexion_sin_propagar_y_limpia(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    openwa.send_audio.side_effect = httpx.ConnectError("dato privado")
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    response_path = tmp_path / "respuesta.ogg"
    response_path.write_bytes(b"respuesta")
    _install_pipeline(
        monkeypatch,
        _response(consultation_id=202, audio_path=str(response_path)),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-audio-error",
    )

    delivered.assert_not_called()
    failed.assert_called_once_with(202, "openwa_connection_error")
    history.assert_not_called()
    assert "dato privado" not in repr(failed.call_args)
    assert not response_path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_sin_respuesta_de_texto_marca_pipeline_no_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    _install_pipeline(
        monkeypatch,
        _response(consultation_id=301, text_response=""),
    )

    await audio_service.AudioService(tmp_path).process_text(
        "consulta",
        "56911111111@c.us",
        "req-texto-vacio",
    )

    openwa.send_text.assert_not_awaited()
    delivered.assert_not_called()
    failed.assert_called_once_with(301, "pipeline_no_response")
    history.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_de_audio_no_cuenta_como_entrega_principal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    fallback_path = tmp_path / "hello.ogg"
    fallback_path.write_bytes(b"fallback")
    monkeypatch.setattr(audio_service, "_HELLO_OGG_PATH", fallback_path)
    _install_pipeline(
        monkeypatch,
        _response(consultation_id=302, audio_path=""),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-audio-vacio",
    )

    openwa.send_audio.assert_awaited_once_with(
        "56911111111@c.us",
        str(fallback_path),
    )
    delivered.assert_not_called()
    failed.assert_called_once_with(302, "pipeline_no_response")
    history.assert_not_called()
    assert fallback_path.exists()


@pytest.mark.asyncio
async def test_bienvenida_y_aviso_legal_no_marcan_fallo_de_entrega(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    welcome_path = tmp_path / "bienvenida.ogg"
    welcome_path.write_bytes(b"bienvenida")
    response_path = tmp_path / "respuesta.ogg"
    response_path.write_bytes(b"respuesta")
    openwa.send_audio.side_effect = [OSError("fallo bienvenida"), {}]
    openwa.send_text.side_effect = RuntimeError("fallo aviso")
    _install_pipeline(
        monkeypatch,
        _response(
            consultation_id=401,
            audio_path=str(response_path),
            welcome_audio_path=str(welcome_path),
            es_primer_contacto=True,
        ),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-onboarding",
    )

    delivered.assert_called_once_with(401)
    failed.assert_not_called()
    history.assert_called_once_with(401)
    assert not welcome_path.exists()
    assert not response_path.exists()


@pytest.mark.asyncio
async def test_consulta_sin_id_no_actualiza_estado_de_entrega(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    _install_pipeline(monkeypatch, _response(consultation_id=None))

    await audio_service.AudioService(tmp_path).process_text(
        "consulta",
        "56911111111@c.us",
        "req-sin-id",
    )

    delivered.assert_not_called()
    failed.assert_not_called()
    history.assert_not_called()


def test_complemento_indap_solo_contiene_links_allowlisted_y_sin_duplicados() -> None:
    """El texto libre y dominios parecidos nunca llegan a la tarjeta."""
    official = "https://www.indap.gob.cl/plataforma-de-servicios/credito-corto-plazo"
    response = (
        f"Texto libre privado. Fuente: {official}. Repetida: {official} "
        "Falsa: https://www.indap.gob.cl.evil.example/robo "
        "Externa: https://example.com/credito"
    )

    message = build_indap_sources_message(response)

    assert message == f"Fuentes oficiales de INDAP:\n• {official}"
    assert "privado" not in message
    assert "example.com" not in message
    assert message.count(official) == 1


@pytest.mark.asyncio
async def test_audio_credito_envia_un_complemento_despues_del_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """La fuente queda escrita y el audio principal conserva su entrega."""
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    send_order: list[str] = []

    async def track_audio(_chat_id: str, _audio_path: str) -> dict[str, object]:
        send_order.append("audio")
        return {}

    async def track_text(_chat_id: str, _message: str) -> dict[str, object]:
        send_order.append("text")
        return {}

    openwa.send_audio.side_effect = track_audio
    openwa.send_text.side_effect = track_text
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    response_path = tmp_path / "respuesta_credito.ogg"
    response_path.write_bytes(b"respuesta")
    official = "https://www.indap.gob.cl/plataforma-de-servicios/credito-corto-plazo"
    _install_pipeline(
        monkeypatch,
        _response(
            consultation_id=501,
            audio_path=str(response_path),
            text_response=f"Información según INDAP: {official}",
            intent="credito",
        ),
    )

    await audio_service.AudioService(tmp_path).process_audio(
        b"audio",
        "56911111111@c.us",
        "req-audio-credito",
    )

    openwa.send_audio.assert_awaited_once_with(
        "56911111111@c.us",
        str(response_path),
    )
    openwa.send_text.assert_awaited_once_with(
        "56911111111@c.us",
        f"Fuentes oficiales de INDAP:\n• {official}",
    )
    assert send_order == ["audio", "text"]
    delivered.assert_called_once_with(501)
    failed.assert_not_called()
    history.assert_called_once_with(501)


@pytest.mark.asyncio
async def test_fallo_del_complemento_no_revierte_entrega_principal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un fallo secundario deja delivered y registra solo código estable."""
    _install_audio_conversion(monkeypatch)
    openwa = _install_openwa(monkeypatch)
    openwa.send_text.side_effect = RuntimeError("https://www.indap.gob.cl/dato-privado-56911111111")
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    response_path = tmp_path / "respuesta_credito.ogg"
    response_path.write_bytes(b"respuesta")
    official = "https://www.indap.gob.cl/plataforma-de-servicios/credito-largo-plazo"
    _install_pipeline(
        monkeypatch,
        _response(
            consultation_id=502,
            audio_path=str(response_path),
            text_response=f"Fuente: {official}",
            intent="credito",
        ),
    )

    with caplog.at_level("WARNING"):
        await audio_service.AudioService(tmp_path).process_audio(
            b"audio",
            "56911111111@c.us",
            "req-audio-fuente-fallida",
        )

    delivered.assert_called_once_with(502)
    failed.assert_not_called()
    history.assert_called_once_with(502)
    openwa.send_text.assert_awaited_once()
    assert "indap_sources_send_failed" in caplog.text
    assert "dato-privado" not in caplog.text
    assert "56911111111" not in caplog.text
    assert "https://" not in caplog.text


@pytest.mark.asyncio
async def test_texto_credito_conserva_respuesta_y_urls_en_un_solo_envio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El canal escrito no agrega una segunda tarjeta redundante."""
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    official = "https://www.indap.gob.cl/plataforma-de-servicios/credito-corto-plazo"
    full_response = f"Información oficial INDAP. Fuente: {official}"
    _install_pipeline(
        monkeypatch,
        _response(
            consultation_id=503,
            text_response=full_response,
            intent="credito",
        ),
    )

    await audio_service.AudioService(tmp_path).process_text(
        "necesito crédito INDAP",
        "56911111111@c.us",
        "req-texto-credito",
    )

    openwa.send_text.assert_awaited_once_with(
        "56911111111@c.us",
        full_response,
    )
    delivered.assert_called_once_with(503)
    failed.assert_not_called()
    history.assert_called_once_with(503)


@pytest.mark.asyncio
async def test_fallo_de_historial_no_revierte_entrega_confirmada(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """El tratamiento secundario de historial nunca altera WhatsApp."""
    openwa = _install_openwa(monkeypatch)
    delivered, failed, history = _install_delivery_spies(monkeypatch)
    history.side_effect = RuntimeError("consulta privada")
    _install_pipeline(monkeypatch, _response(consultation_id=504))

    with caplog.at_level("WARNING"):
        await audio_service.AudioService(tmp_path).process_text(
            "precio privado",
            "56911111111@c.us",
            "req-historial-fallido",
        )

    openwa.send_text.assert_awaited_once()
    delivered.assert_called_once_with(504)
    failed.assert_not_called()
    history.assert_called_once_with(504)
    assert "historial post-entrega" in caplog.text
    assert "consulta privada" not in caplog.text
    assert "precio privado" not in caplog.text
    assert "56911111111" not in caplog.text
