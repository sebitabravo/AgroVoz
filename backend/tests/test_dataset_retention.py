"""Tests para la retención selectiva de audio del dataset de voz rural (#96).

Cubre:
- ``dataset_service.retain_audio`` respeta ``dataset_consent`` (opt-in).
- Sin UserPrefs o sin consentimiento no se retiene nada (regresión de privacidad).
- El manifest JSONL tiene los campos requeridos y usa ``phone_hash``.
- ``pipeline_service`` retiene audio solo cuando corresponde.
- ``scripts/export_dataset.py`` genera artefactos CSV/JSON/zip.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.models.user_prefs import UserPrefs
from app.services.dataset_service import (
    has_dataset_consent,
    load_manifest_entries,
    retain_audio,
)
from app.services.pipeline_service import AgroVozPipeline

# Hash válido de 64 caracteres hex minúscula.
_VALID_HASH = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def _create_user_prefs(
    db: Any,
    phone_hash: str,
    *,
    consent: bool = False,
    comuna: str = "Traiguén",
) -> UserPrefs:
    """Helper para crear UserPrefs en la DB temporal del test."""
    prefs = UserPrefs(phone_hash=phone_hash, comuna=comuna, dataset_consent=consent)
    db.add(prefs)
    db.commit()
    return prefs


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    """Directorio temporal para el dataset de cada test."""
    return tmp_path / "dataset"


@pytest.fixture
def sample_wav(tmp_path: Path) -> Path:
    """Archivo WAV falso para usar como input."""
    p = tmp_path / "input.wav"
    p.write_bytes(b"FAKE_WAV_16KHZ_MONO")
    return p


# ── has_dataset_consent ────────────────────────────────────────────


class TestHasDatasetConsent:
    """Consulta de consentimiento con safe defaults."""

    def test_sin_user_prefs_retorna_false(self, db: Any) -> None:
        """Si no hay UserPrefs, no hay consentimiento."""
        assert has_dataset_consent(_VALID_HASH, db=db) is False

    def test_user_prefs_sin_consentimiento_retorna_false(self, db: Any) -> None:
        """Default dataset_consent=False no permite retención."""
        _create_user_prefs(db, _VALID_HASH, consent=False)
        assert has_dataset_consent(_VALID_HASH, db=db) is False

    def test_user_prefs_con_consentimiento_retorna_true(self, db: Any) -> None:
        """Solo con opt-in explicito se permite retención."""
        _create_user_prefs(db, _VALID_HASH, consent=True)
        assert has_dataset_consent(_VALID_HASH, db=db) is True

    def test_phone_hash_invalido_retorna_false(self, db: Any) -> None:
        """Hash con formato inválido nunca retiene."""
        assert has_dataset_consent("no-es-un-hash", db=db) is False

    def test_sin_chat_retorna_false(self, db: Any) -> None:
        """chat_id_hash='sin_chat' no retiene."""
        assert has_dataset_consent("sin_chat", db=db) is False


# ── retain_audio ───────────────────────────────────────────────────


class TestRetainAudio:
    """Retención selectiva de muestras de audio."""

    def test_retiene_audio_con_consentimiento(
        self,
        db: Any,
        dataset_dir: Path,
        sample_wav: Path,
    ) -> None:
        """Con dataset_consent=True se copia el wav y se registra en manifest."""
        _create_user_prefs(db, _VALID_HASH, consent=True)

        retained = retain_audio(
            sample_wav,
            _VALID_HASH,
            "precio de la papa",
            2500,
            dataset_dir=dataset_dir,
            db=db,
        )

        assert retained is not None
        assert retained.exists()
        assert retained.read_bytes() == sample_wav.read_bytes()
        assert _VALID_HASH in str(retained)

        entries = load_manifest_entries(dataset_dir)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["transcripcion_whisper"] == "precio de la papa"
        assert entry["duracion_ms"] == 2500
        assert entry["phone_hash"] == _VALID_HASH
        assert entry["transcripcion_verificada"] is None
        assert "fecha" in entry
        assert "audio_path" in entry

    def test_no_retiene_sin_consentimiento(
        self,
        db: Any,
        dataset_dir: Path,
        sample_wav: Path,
    ) -> None:
        """Con dataset_consent=False no se copia ni se toca el manifest."""
        _create_user_prefs(db, _VALID_HASH, consent=False)

        retained = retain_audio(
            sample_wav,
            _VALID_HASH,
            "precio de la papa",
            2500,
            dataset_dir=dataset_dir,
            db=db,
        )

        assert retained is None
        assert not dataset_dir.exists() or not any(dataset_dir.iterdir())
        assert load_manifest_entries(dataset_dir) == []

    def test_manifest_falla_borra_audio_copiado(
        self,
        db: Any,
        dataset_dir: Path,
        sample_wav: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Si el manifest no se puede escribir, no queda audio huérfano (atomicidad)."""
        _create_user_prefs(db, _VALID_HASH, consent=True)

        import builtins

        original_open = builtins.open

        def fake_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            if "manifest.jsonl" in str(file):
                raise OSError("disco lleno")
            return original_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)

        retained = retain_audio(
            sample_wav,
            _VALID_HASH,
            "precio de la papa",
            2500,
            dataset_dir=dataset_dir,
            db=db,
        )

        assert retained is None
        # La copia del wav se revierte: sin entrada en manifest no debe
        # quedar muestra en disco (quedaría invisible para eval_wer/export).
        sample_subdir = dataset_dir / _VALID_HASH
        assert not sample_subdir.exists() or not any(sample_subdir.glob("*.wav"))

    def test_no_retiene_sin_user_prefs(
        self,
        db: Any,
        dataset_dir: Path,
        sample_wav: Path,
    ) -> None:
        """Sin fila UserPrefs no se retiene (safe default)."""
        retained = retain_audio(
            sample_wav,
            _VALID_HASH,
            "precio de la papa",
            2500,
            dataset_dir=dataset_dir,
            db=db,
        )

        assert retained is None
        assert load_manifest_entries(dataset_dir) == []

    def test_ruta_no_contiene_numero_real(
        self,
        db: Any,
        dataset_dir: Path,
        sample_wav: Path,
    ) -> None:
        """El path destino contiene phone_hash, nunca el número en claro."""
        _create_user_prefs(db, _VALID_HASH, consent=True)
        numero_real = "+56912345678"

        retained = retain_audio(
            sample_wav,
            _VALID_HASH,
            "hola",
            1000,
            dataset_dir=dataset_dir,
            db=db,
        )

        assert retained is not None
        assert _VALID_HASH in str(retained)
        assert numero_real not in str(retained)


# ── Pipeline integration ───────────────────────────────────────────


@pytest.mark.asyncio
class TestPipelineDatasetRetention:
    """El pipeline invoca retención de dataset en el momento correcto (#96)."""

    @pytest.fixture
    def fake_tts_path(self, tmp_path: Path) -> Path:
        """Archivo OGG falso para simular salida de TTS."""
        p = tmp_path / "tts_output.ogg"
        p.write_bytes(b"FAKE_TTS_OGG")
        return p

    async def test_pipeline_llama_retain_audio_con_transcripcion(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_wav: Path,
        fake_tts_path: Path,
    ) -> None:
        """Pipeline pasa wav, phone_hash, transcripcion y duracion a retain_audio."""
        calls: list[tuple[Path, str, str, int]] = []

        def fake_retain_audio(
            wav_path: Path,
            phone_hash: str,
            transcription: str,
            duration_ms: int,
        ) -> Path | None:
            calls.append((wav_path, phone_hash, transcription, duration_ms))
            return None

        def fake_transcribe(_self: object, _audio_path: str) -> dict[str, object]:
            return {
                "text": "¿cuál es el precio de la papa?",
                "language": "es",
                "segments": [],
                "duration_ms": 1200,
            }

        async def fake_answer(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            return "La papa está a 450 pesos el kilo."

        def fake_synthesize(_self: object, _text: str) -> str:
            return str(fake_tts_path)

        monkeypatch.setattr(
            "app.services.pipeline_service.retain_audio",
            fake_retain_audio,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe,
        )
        monkeypatch.setattr("app.services.llm_service.answer", fake_answer)
        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize,
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_is_first_contact",
            staticmethod(lambda _phone_hash: False),
        )

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=sample_wav,
            audio_duration_ms=2500,
            message_id="msg-retain-call",
            chat_id_hash=_VALID_HASH,
            request_id="req-retain-call",
        )

        assert result.audio_path == str(fake_tts_path)
        assert len(calls) == 1
        assert calls[0][0] == sample_wav
        assert calls[0][1] == _VALID_HASH
        assert "papa" in calls[0][2]
        assert calls[0][3] == 2500

    async def test_pipeline_no_llama_retain_audio_sin_transcripcion(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_wav: Path,
        fake_tts_path: Path,
    ) -> None:
        """Si Whisper no produce texto, no se invoca retención."""
        calls: list[tuple[Path, str, str, int]] = []

        def fake_retain_audio(*args: object, **kwargs: object) -> Path | None:
            calls.append(args)  # type: ignore[arg-type]
            return None

        def fake_transcribe(_self: object, _audio_path: str) -> dict[str, object]:
            return {"text": "", "language": "es", "segments": [], "duration_ms": 0}

        def fake_synthesize(_self: object, _text: str) -> str:
            return str(fake_tts_path)

        monkeypatch.setattr(
            "app.services.pipeline_service.retain_audio",
            fake_retain_audio,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize,
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_is_first_contact",
            staticmethod(lambda _phone_hash: False),
        )

        pipeline = AgroVozPipeline()
        await pipeline.process(
            wav_path=sample_wav,
            audio_duration_ms=1000,
            message_id="msg-empty",
            chat_id_hash=_VALID_HASH,
            request_id="req-empty",
        )

        assert calls == []

    async def test_pipeline_no_llama_retain_audio_sin_chat(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_wav: Path,
        fake_tts_path: Path,
    ) -> None:
        """chat_id_hash='sin_chat' no invoca retención."""
        calls: list[tuple[Path, str, str, int]] = []

        def fake_retain_audio(*args: object, **kwargs: object) -> Path | None:
            calls.append(args)  # type: ignore[arg-type]
            return None

        def fake_transcribe(_self: object, _audio_path: str) -> dict[str, object]:
            return {"text": "hola", "language": "es", "segments": [], "duration_ms": 500}

        def fake_synthesize(_self: object, _text: str) -> str:
            return str(fake_tts_path)

        monkeypatch.setattr(
            "app.services.pipeline_service.retain_audio",
            fake_retain_audio,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize,
        )

        pipeline = AgroVozPipeline()
        await pipeline.process(
            wav_path=sample_wav,
            audio_duration_ms=1000,
            message_id="msg-sin-chat",
            chat_id_hash="sin_chat",
            request_id="req-sin-chat",
        )

        assert calls == []


# ── Export script ──────────────────────────────────────────────────


class TestExportDataset:
    """Tests para scripts/export_dataset.py."""

    @pytest.fixture
    def populated_dataset(self, tmp_path: Path) -> Path:
        """Dataset con dos muestras para probar exportacion."""
        dataset_dir = tmp_path / "dataset"
        sample_dir = dataset_dir / _VALID_HASH
        sample_dir.mkdir(parents=True)

        audio1 = sample_dir / "20260712_120000_aaaaaaaa.wav"
        audio2 = sample_dir / "20260712_130000_bbbbbbbb.wav"
        audio1.write_bytes(b"AUDIO1")
        audio2.write_bytes(b"AUDIO2")

        manifest = dataset_dir / "manifest.jsonl"
        entries = [
            {
                "audio_path": f"{_VALID_HASH}/{audio1.name}",
                "transcripcion_whisper": "precio de la papa",
                "transcripcion_verificada": "precio de la papa",
                "duracion_ms": 2000,
                "fecha": "2026-07-12T12:00:00+00:00",
                "phone_hash": _VALID_HASH,
            },
            {
                "audio_path": f"{_VALID_HASH}/{audio2.name}",
                "transcripcion_whisper": "como esta el clima",
                "transcripcion_verificada": None,
                "duracion_ms": 1500,
                "fecha": "2026-07-12T13:00:00+00:00",
                "phone_hash": _VALID_HASH,
            },
        ]
        with open(manifest, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return dataset_dir

    def test_export_csv(self, populated_dataset: Path, tmp_path: Path) -> None:
        """Exporta manifest.csv con las columnas esperadas."""
        from scripts.export_dataset import main

        output = tmp_path / "export.csv"
        rc = main(["--format", "csv", "--dataset-dir", str(populated_dataset), "--output", str(output)])

        assert rc == 0
        assert output.exists()
        text = output.read_text(encoding="utf-8")
        assert "audio_path,transcripcion_whisper" in text
        assert "precio de la papa" in text

    def test_export_json_para_eval_wer(self, populated_dataset: Path, tmp_path: Path) -> None:
        """Exporta manifest.json compatible con scripts/eval_wer.py."""
        from scripts.export_dataset import main

        output = tmp_path / "eval.json"
        rc = main(["--format", "json", "--dataset-dir", str(populated_dataset), "--output", str(output)])

        assert rc == 0
        data = json.loads(output.read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[0]["audio"] == f"{_VALID_HASH}/20260712_120000_aaaaaaaa.wav"
        assert data[0]["text"] == "precio de la papa"
        assert "duration_s" in data[0]

    def test_export_zip(self, populated_dataset: Path, tmp_path: Path) -> None:
        """Exporta .zip con audios y manifest.csv."""
        from scripts.export_dataset import main

        output = tmp_path / "export.zip"
        rc = main(["--format", "zip", "--dataset-dir", str(populated_dataset), "--output", str(output)])

        assert rc == 0
        assert output.exists()
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            assert "manifest.csv" in names
            assert f"{_VALID_HASH}/20260712_120000_aaaaaaaa.wav" in names

    def test_export_filtra_por_phone_hash(self, populated_dataset: Path, tmp_path: Path) -> None:
        """El filtro --phone-hash reduce las muestras exportadas."""
        from scripts.export_dataset import main

        output = tmp_path / "filtered.csv"
        other_hash = "b" * 64
        rc = main([
            "--format", "csv",
            "--dataset-dir", str(populated_dataset),
            "--phone-hash", other_hash,
            "--output", str(output),
        ])

        assert rc == 1  # No hay muestras para ese phone_hash.
        assert not output.exists()
