"""Tests para prompt_builder — secciones del system prompt (issue #190)."""

from app.services.prompt_builder import (
    CONTEXTO,
    DERIVACION,
    EJEMPLOS,
    HERRAMIENTAS,
    LIMITES,
    REGLAS,
    build_system_prompt,
)


class TestSeccionesPresentes:
    """Verifica que cada sección esté presente en el prompt final."""

    def test_contexto_presente(self) -> None:
        """El prompt DEBE contener la sección de Contexto."""
        prompt = build_system_prompt()
        assert CONTEXTO in prompt, "CONTEXTO no está en el prompt"

    def test_limites_presente(self) -> None:
        """El prompt DEBE contener la sección de Límites."""
        prompt = build_system_prompt()
        assert LIMITES in prompt, "LIMITES no está en el prompt"

    def test_ejemplos_presente(self) -> None:
        """El prompt DEBE contener la sección de Ejemplos."""
        prompt = build_system_prompt()
        assert EJEMPLOS in prompt, "EJEMPLOS no está en el prompt"

    def test_reglas_presente(self) -> None:
        """El prompt DEBE contener la sección de Reglas."""
        prompt = build_system_prompt()
        assert REGLAS in prompt, "REGLAS no está en el prompt"

    def test_derivacion_presente(self) -> None:
        """El prompt DEBE contener la sección de Derivación."""
        prompt = build_system_prompt()
        assert DERIVACION in prompt, "DERIVACION no está en el prompt"

    def test_herramientas_presente(self) -> None:
        """El prompt DEBE contener la sección de Herramientas."""
        prompt = build_system_prompt()
        assert HERRAMIENTAS in prompt, "HERRAMIENTAS no está en el prompt"


class TestPromptNoInflado:
    """Verifica que el prompt no exceda el límite de caracteres."""

    def test_total_prompt_chars_under_limit(self) -> None:
        """El prompt construido NO debe superar el límite de 10,100 chars."""
        prompt = build_system_prompt()
        assert len(prompt) <= 10100, (
            f"Prompt excede límite: {len(prompt)} chars (máx 10,100)"
        )

    def test_prompt_no_vacio(self) -> None:
        """El prompt construido no debe estar vacío."""
        prompt = build_system_prompt()
        assert len(prompt) > 0, "El prompt está vacío"


class TestSinContenidoNexorVentas:
    """Verifica que NO haya contenido de ventas de Nexor."""

    def test_sin_objection_handling(self) -> None:
        """El prompt NO debe contener términos de objection handling."""
        prompt = build_system_prompt()
        assert "objection" not in prompt.lower(), (
            "El prompt contiene 'objection' (descartado de Nexor)"
        )

    def test_sin_lead_qualification(self) -> None:
        """El prompt NO debe contener términos de calificación de leads."""
        prompt = build_system_prompt()
        assert "lead" not in prompt.lower(), (
            "El prompt contiene 'lead' (descartado de Nexor)"
        )

    def test_sin_spam(self) -> None:
        """El prompt NO debe contener términos de descarte de spam."""
        prompt = build_system_prompt()
        assert "spam" not in prompt.lower(), (
            "El prompt contiene 'spam' (descartado de Nexor)"
        )


class TestModificacionAislada:
    """Verifica que cada sección sea accesible de forma independiente."""

    def test_secciones_independientes(self) -> None:
        """Cada sección existe como constante independiente y es testeable."""
        assert len(CONTEXTO) > 0
        assert len(LIMITES) > 0
        assert len(EJEMPLOS) > 0
        assert len(REGLAS) > 0
        assert len(DERIVACION) > 0
        assert len(HERRAMIENTAS) > 0
