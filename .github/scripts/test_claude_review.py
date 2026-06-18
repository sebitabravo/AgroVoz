"""Tests de regresión para el agente de code review (claude-review.py).

Cubren las dos costuras donde el pipeline confiaba en salida cruda del LLM y podía
descartar un hallazgo REAL en silencio (falso negativo — la peor clase de fallo
para un líder técnico que delega la review):

  1. Join Find<->Verify por id (verify_findings): el LLM puede devolver finding_id
     como str ("0") aunque Find lo emita como int (0). Sin normalizar a str en ambos
     lados, el lookup falla y el hallazgo real cae al default 'false_positive'.

  2. Bucketing por severidad (build_review_markdown): el LLM puede emitir "critical"/
     "p0"/"P1 " en vez del schema P0|P1|P2. Sin normalizar, el hallazgo no entra a
     ningún bucket, no se renderiza y el safety net aprueba el PR como si no hubiera bugs.

NO se testea el fix del literal "{10}s" (print en except TimeoutExpired): es un string
cosmético sin lógica; un test de subprocess-timeout sería frágil y de bajo valor.
Lo cubren ruff + revisión visual.

Correr (con la versión de anthropic pineada en CI):
    uvx --with anthropic==0.49.0 python3 .github/scripts/test_claude_review.py
"""

import importlib.util
import pathlib
import unittest

_SCRIPT = pathlib.Path(__file__).parent / "claude-review.py"
_spec = importlib.util.spec_from_file_location("claude_review", _SCRIPT)
assert _spec and _spec.loader
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


class VerifyFindingsIdJoinTest(unittest.TestCase):
    """Fix ①: el join por id debe sobrevivir a un finding_id str vs int."""

    def setUp(self) -> None:
        # Aislar verify_findings de la red y del filesystem.
        self._orig_api = cr.api_call
        self._orig_ctx = cr.get_code_context
        self._orig_valid = cr._validate_file_path
        # El LLM devuelve finding_id como STRING aunque Find lo stampea como int.
        cr.api_call = lambda *a, **k: (
            '{"verified": [{"finding_id": "0", "verdict": "real", '
            '"explanation": "bug confirmado"}]}'
        )
        cr.get_code_context = lambda *a, **k: "codigo real de contexto"
        cr._validate_file_path = lambda *a, **k: True

    def tearDown(self) -> None:
        cr.api_call = self._orig_api
        cr.get_code_context = self._orig_ctx
        cr._validate_file_path = self._orig_valid

    def test_matches_finding_when_llm_returns_id_as_string(self) -> None:
        findings = [
            {"file": "app/x.py", "line": 5, "severity": "P0", "title": "Bug real"}
        ]
        result = cr.verify_findings(client=None, findings=findings, review_md="")
        self.assertEqual(
            len(result), 1,
            "Un hallazgo verificado como 'real' no debe descartarse por el id ser "
            "str en Verify e int en Find.",
        )
        self.assertEqual(result[0]["title"], "Bug real")

    def test_stamps_deterministic_id_ignoring_llm_id(self) -> None:
        # El id que mande el LLM en el finding original se ignora: se re-stampea por
        # posición. Dos findings sin id no colisionan ni se pierden.
        cr.api_call = lambda *a, **k: (
            '{"verified": ['
            '{"finding_id": 0, "verdict": "real", "explanation": "a"},'
            '{"finding_id": 1, "verdict": "false_positive", "explanation": "b"}'
            ']}'
        )
        findings = [
            {"file": "a.py", "line": 1, "severity": "P0", "title": "Real", "id": 99},
            {"file": "b.py", "line": 2, "severity": "P1", "title": "Falso", "id": 99},
        ]
        result = cr.verify_findings(client=None, findings=findings, review_md="")
        titles = [f["title"] for f in result]
        self.assertEqual(titles, ["Real"])


class SeverityNormalizationTest(unittest.TestCase):
    """Fix ②: severidad off-schema no debe perder el hallazgo ni aprobar el PR."""

    @staticmethod
    def _assessment() -> dict:
        # Assessment "optimista" del LLM: si el hallazgo se perdiera del bucket,
        # estos valores aprobarían el PR.
        return {
            "quality_score": 9,
            "merge_risk": "LOW",
            "verdict": "APPROVE",
            "overall_assessment": "GOOD",
            "checklist": [],
            "riesgos_residuales": [],
            "alcance": {},
            "improvement_suggestions": [],
        }

    def test_critical_severity_is_bucketed_as_p0_and_blocks(self) -> None:
        verified = [{"severity": "critical", "title": "INYECCION_SQL", "line": 10,
                     "file": "app/db.py", "description": "x", "proposed_fix": "y"}]
        md = cr.build_review_markdown({}, verified, self._assessment())
        # El hallazgo NO se pierde (sin el fix no se renderiza en ningún bucket).
        self.assertIn("INYECCION_SQL", md)
        # P0 fuerza riesgo HIGH; sin el fix el safety net dejaría NINGUNO/LOW.
        self.assertIn("HIGH", md)
        # Sin el fix, 0 buckets → safety net marca EXCELENTE (aprobaría el bug).
        self.assertNotIn("EXCELENTE", md)

    def test_lowercase_and_whitespace_severity_normalized(self) -> None:
        verified = [{"severity": " p1 ", "title": "VALIDACION_FALTANTE", "line": 3,
                     "file": "app/api.py", "description": "x", "proposed_fix": "y"}]
        md = cr.build_review_markdown({}, verified, self._assessment())
        self.assertIn("VALIDACION_FALTANTE", md)

    def test_clean_diff_still_approves(self) -> None:
        # Regresión inversa: sin hallazgos, el safety net debe seguir aprobando.
        md = cr.build_review_markdown({}, [], self._assessment())
        self.assertIn("EXCELENTE", md)


class _TextBlock:
    """Bloque de respuesta con texto (lo que el parseo debe extraer)."""

    def __init__(self, text: str) -> None:
        self.text = text


class _ThinkingBlock:
    """Bloque de razonamiento (reasoning_effort=max): sin .text, debe saltarse."""

    def __init__(self, thinking: str) -> None:
        self.thinking = thinking


class ExtractTextTest(unittest.TestCase):
    """reasoning_effort=max puede anteponer bloques thinking sin .text.

    El parseo no debe asumir content[0]: debe saltar thinking y devolver el texto.
    Sin esto, un bloque de razonamiento al frente reventaría el parseo y el
    fail-closed descartaría toda la review.
    """

    def test_single_text_block(self) -> None:
        self.assertEqual(cr._extract_text([_TextBlock("hola")]), "hola")

    def test_skips_leading_thinking_block(self) -> None:
        blocks = [_ThinkingBlock("razonando..."), _TextBlock('{"verified": []}')]
        self.assertEqual(cr._extract_text(blocks), '{"verified": []}')

    def test_concatenates_multiple_text_blocks(self) -> None:
        blocks = [_TextBlock('{"a":'), _TextBlock(" 1}")]
        self.assertEqual(cr._extract_text(blocks), '{"a": 1}')

    def test_raises_when_no_text_block(self) -> None:
        with self.assertRaises(RuntimeError):
            cr._extract_text([_ThinkingBlock("solo razonamiento, sin respuesta")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
