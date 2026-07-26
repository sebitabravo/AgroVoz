"""Tests del aviso de responsabilidad del primer contacto.

El aviso no es una cortesia de onboarding: es el descargo que limita el alcance
de lo que AgroVoz entrega (datos oficiales, no recomendaciones) y la unica via
por la que el productor lo recibe dentro del producto. El acuerdo firmado en
terreno cubre a los 3-5 del piloto; el aviso en WhatsApp cubre a cualquiera que
escriba despues.

Por eso se prueba que llega en LOS DOS caminos de entrada y que sobrevive a que
falle el audio de bienvenida.
"""

import pytest

from app.services.audio_service import AudioService
from app.services.pipeline_service import (
    WELCOME_DISCLAIMER_TEXT,
    AgroVozPipeline,
)


class _OpenWAEspia:
    """Doble de Open-WA que registra los textos enviados."""

    def __init__(self, falla: bool = False) -> None:
        self.textos: list[str] = []
        self._falla = falla

    async def send_text(self, _chat_id: str, texto: str) -> None:
        if self._falla:
            raise RuntimeError("Open-WA caido")
        self.textos.append(texto)


class TestContenidoDelAviso:
    """Lo que el aviso tiene que decir para servir como descargo."""

    def test_aclara_que_no_es_recomendacion(self) -> None:
        """El productor decide; AgroVoz solo informa."""
        assert "NO una recomendacion" in WELCOME_DISCLAIMER_TEXT

    def test_advierte_que_el_precio_es_mayorista(self) -> None:
        """Sin esto el productor cree que le mienten cuando le ofrecen menos."""
        assert "mayorista" in WELCOME_DISCLAIMER_TEXT
        assert "piso del mercado" in WELCOME_DISCLAIMER_TEXT

    def test_deslinda_responsabilidad_por_la_decision_de_venta(self) -> None:
        assert "no se hace responsable" in WELCOME_DISCLAIMER_TEXT

    def test_deriva_a_prodesal_para_asesoria(self) -> None:
        """El equipo no tiene formacion agronomica: la asesoria va a INDAP."""
        assert "PRODESAL" in WELCOME_DISCLAIMER_TEXT

    def test_atribuye_las_fuentes(self) -> None:
        """Open-Meteo es CC BY 4.0: la atribucion es obligatoria."""
        assert "ODEPA" in WELCOME_DISCLAIMER_TEXT
        assert "Open-Meteo" in WELCOME_DISCLAIMER_TEXT


class TestDeteccionDePrimerContacto:
    """La deteccion corre en ambos caminos, no solo en el de audio."""

    async def test_texto_marca_primer_contacto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regresion: antes la deteccion estaba detras de generar_audio."""
        monkeypatch.setattr(
            AgroVozPipeline, "_is_first_contact", staticmethod(lambda _h: True)
        )

        async def responder(*_a: object, **_k: object) -> str:
            return "La papa está a 520 pesos el kilo."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", responder)

        resultado = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="aviso-1",
            chat_id_hash="d" * 64,
            request_id="req-aviso-1",
            texto_directo="¿a cuánto está la papa?",
            generar_audio=False,
        )

        assert resultado.es_primer_contacto is True

    async def test_texto_no_sintetiza_bienvenida(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A quien escribe no se le manda un audio de bienvenida."""
        monkeypatch.setattr(
            AgroVozPipeline, "_is_first_contact", staticmethod(lambda _h: True)
        )
        tts_llamado: list[int] = []

        def tts_espia(*_a: object, **_k: object) -> str:
            tts_llamado.append(1)
            return "/tmp/no.ogg"

        monkeypatch.setattr("app.services.tts_service.TTSService.synthesize", tts_espia)

        async def responder(*_a: object, **_k: object) -> str:
            return "La papa está a 520 pesos el kilo."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", responder)

        resultado = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="aviso-2",
            chat_id_hash="e" * 64,
            request_id="req-aviso-2",
            texto_directo="¿a cuánto está la papa?",
            generar_audio=False,
        )

        assert resultado.es_primer_contacto is True
        assert resultado.welcome_audio_path is None
        assert tts_llamado == [], "el camino de texto no sintetiza bienvenida"

    async def test_contacto_conocido_no_reenvia_el_aviso(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El aviso va una vez, no en cada consulta."""
        monkeypatch.setattr(
            AgroVozPipeline, "_is_first_contact", staticmethod(lambda _h: False)
        )

        async def responder(*_a: object, **_k: object) -> str:
            return "La papa está a 520 pesos el kilo."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", responder)

        resultado = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="aviso-3",
            chat_id_hash="f" * 64,
            request_id="req-aviso-3",
            texto_directo="¿a cuánto está la papa?",
            generar_audio=False,
        )

        assert resultado.es_primer_contacto is False


class TestEnvioDelAviso:
    """El envio propiamente tal."""

    async def test_envia_el_texto_del_aviso(self) -> None:
        openwa = _OpenWAEspia()

        await AudioService._enviar_aviso_responsabilidad(
            openwa,  # type: ignore[arg-type]
            "56900000000@c.us",
            "req-envio-1",
        )

        assert openwa.textos == [WELCOME_DISCLAIMER_TEXT]

    async def test_si_el_envio_falla_no_revienta_la_consulta(self) -> None:
        """El aviso es importante, pero no puede tumbar la respuesta al productor."""
        openwa = _OpenWAEspia(falla=True)

        await AudioService._enviar_aviso_responsabilidad(
            openwa,  # type: ignore[arg-type]
            "56900000000@c.us",
            "req-envio-2",
        )

        assert openwa.textos == []
