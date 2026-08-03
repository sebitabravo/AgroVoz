"""Pruebas contractuales del viewfinder móvil del panel PWA."""

from pathlib import Path

_STATIC_PANEL = Path(__file__).parents[1] / "app" / "static" / "panel"


def test_panel_expone_seccion_de_identificacion_y_camara() -> None:
    """El shell contiene controles accesibles incluso en viewport estrecho."""
    html = (_STATIC_PANEL / "index.html").read_text(encoding="utf-8")

    assert 'id="identificar-plaga"' in html
    assert 'id="camara-viewfinder"' in html
    assert 'id="imagen-galeria"' in html
    assert 'accept="image/jpeg,image/png,image/webp"' in html


def test_panel_activa_camara_trasera_y_envia_multipart() -> None:
    """El cliente usa getUserMedia y el contrato multipart del endpoint."""
    javascript = (_STATIC_PANEL / "app.js").read_text(encoding="utf-8")

    assert 'getUserMedia({ video: { facingMode: "environment" }, audio: false })' in javascript
    assert 'fetch("/api/v1/vision/identify"' in javascript
    assert 'formData.append("image"' in javascript
    assert "Fuente INIA" in javascript


def test_service_worker_invalida_shell_anterior() -> None:
    """Los dispositivos instalados deben recibir el cliente con cámara."""
    service_worker = (_STATIC_PANEL / "sw.js").read_text(encoding="utf-8")

    assert 'const CACHE_NAME = "agrovoz-panel-v2"' in service_worker
