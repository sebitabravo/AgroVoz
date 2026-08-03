/**
 * Cliente del panel del agricultor (C3).
 *
 * El shell HTML es el mismo para cualquier link: el token vive en la URL
 * (/panel/{token}) y este script lo lee para pedir el resumen a la API.
 * No hay login ni sesion: el token en la URL ES la unica credencial.
 */
(function () {
  "use strict";

  function extraerToken() {
    var partes = window.location.pathname.split("/").filter(Boolean);
    // partes = ["panel", "<token>"]
    return partes.length >= 2 ? partes[1] : "";
  }

  function escaparHtml(texto) {
    var div = document.createElement("div");
    div.textContent = texto;
    return div.innerHTML;
  }

  function renderizarResumen(resumen) {
    var contenido = document.getElementById("contenido");
    var partes = [];

    partes.push(
      '<div class="card"><h2>Comuna</h2><p>' +
        escaparHtml(resumen.comuna || "No registrada") +
        "</p></div>",
    );

    if (resumen.cultivos && resumen.cultivos.length) {
      partes.push(
        '<div class="card"><h2>Cultivos de interés</h2><p>' +
          resumen.cultivos.map(escaparHtml).join(", ") +
          "</p></div>",
      );
    }

    if (resumen.parcelas && resumen.parcelas.length) {
      var filasParcelas = resumen.parcelas
        .map(function (p) {
          return (
            "<li>" +
            escaparHtml(p.cultivo) +
            " — " +
            escaparHtml(String(p.superficie_ha)) +
            " ha en " +
            escaparHtml(p.comuna) +
            "</li>"
          );
        })
        .join("");
      partes.push('<div class="card"><h2>Mis parcelas</h2><ul>' + filasParcelas + "</ul></div>");
    }

    if (resumen.alertas && resumen.alertas.length) {
      var filasAlertas = resumen.alertas
        .map(function (a) {
          var detalle = a.producto ? escaparHtml(a.producto) : "clima";
          return "<li>" + escaparHtml(a.tipo) + " — " + detalle + "</li>";
        })
        .join("");
      partes.push('<div class="card"><h2>Mis alertas activas</h2><ul>' + filasAlertas + "</ul></div>");
    }

    if (partes.length === 1) {
      partes.push('<p class="muted">Todavía no tienes parcelas ni alertas registradas.</p>');
    }

    contenido.innerHTML = partes.join("");
  }

  function mostrarError(mensaje) {
    document.getElementById("contenido").innerHTML =
      '<p class="muted">' + escaparHtml(mensaje) + "</p>";
  }

  var cameraStream = null;
  var capturedObjectUrl = "";

  function mostrarEstadoCamara(mensaje, esError) {
    var estado = document.getElementById("camara-estado");
    estado.textContent = mensaje;
    estado.setAttribute("role", esError ? "alert" : "status");
  }

  function detenerCamara() {
    if (cameraStream) {
      cameraStream.getTracks().forEach(function (track) {
        track.stop();
      });
      cameraStream = null;
    }
    document.getElementById("camara-viewfinder").hidden = true;
    document.getElementById("capturar-imagen").hidden = true;
    document.getElementById("capturar-imagen").disabled = true;
    document.getElementById("detener-camara").hidden = true;
  }

  function activarCamara() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      mostrarEstadoCamara("Tu navegador requiere HTTPS para usar la cámara.", true);
      return;
    }
    mostrarEstadoCamara("Solicitando permiso para la cámara trasera…", false);
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(function (stream) {
        cameraStream = stream;
        var viewfinder = document.getElementById("camara-viewfinder");
        viewfinder.srcObject = stream;
        viewfinder.hidden = false;
        document.getElementById("capturar-imagen").hidden = false;
        document.getElementById("capturar-imagen").disabled = false;
        document.getElementById("detener-camara").hidden = false;
        mostrarEstadoCamara("Centra la hoja y captura cuando esté enfocada.", false);
      })
      .catch(function () {
        mostrarEstadoCamara("No pude abrir la cámara. Puedes elegir una foto del dispositivo.", true);
      });
  }

  function mostrarImagenCapturada(blob) {
    if (capturedObjectUrl) URL.revokeObjectURL(capturedObjectUrl);
    capturedObjectUrl = URL.createObjectURL(blob);
    var image = document.getElementById("imagen-capturada");
    image.src = capturedObjectUrl;
    image.hidden = false;
  }

  function renderizarResultadoVision(resultado) {
    var resultadoElement = document.getElementById("resultado-vision");
    var porcentaje = Math.round(Number(resultado.confidence || 0) * 100);
    var titulo = resultado.classification || "No identificado con certeza";
    var candidato = resultado.classification
      ? ""
      : '<p class="muted">Etiqueta candidata: ' + escaparHtml(resultado.detected_label || "sin dato") + "</p>";
    var cita = "";
    if (resultado.source) {
      cita = '<p class="source"><strong>Fuente INIA:</strong> ' + escaparHtml(resultado.source);
      if (resultado.source_url && /^https:\/\//i.test(resultado.source_url)) {
        cita +=
          ' — <a href="' +
          escaparHtml(resultado.source_url) +
          '" target="_blank" rel="noopener">ver fuente</a>';
      }
      cita += "</p>";
    }
    if (resultado.rule) {
      cita += '<p class="muted">' + escaparHtml(resultado.rule) + "</p>";
    }
    resultadoElement.innerHTML =
      "<p><strong>" +
      escaparHtml(titulo) +
      "</strong> — confianza " +
      porcentaje +
      "%</p><p>" +
      escaparHtml(resultado.message || "") +
      "</p>" +
      candidato +
      cita;
  }

  function enviarImagen(blob) {
    mostrarImagenCapturada(blob);
    document.getElementById("resultado-vision").innerHTML =
      '<p class="muted">Analizando la imagen localmente…</p>';
    var formData = new FormData();
    formData.append("image", blob, "captura.jpg");
    fetch("/api/v1/vision/identify?token=" + encodeURIComponent(token), {
      method: "POST",
      body: formData,
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) throw new Error(payload.detail || "No se pudo analizar la imagen.");
          return payload;
        });
      })
      .then(renderizarResultadoVision)
      .catch(function (error) {
        document.getElementById("resultado-vision").innerHTML =
          '<p class="muted">' + escaparHtml(error.message) + "</p>";
      });
  }

  function capturarImagen() {
    var viewfinder = document.getElementById("camara-viewfinder");
    if (!cameraStream || !viewfinder.videoWidth || !viewfinder.videoHeight) {
      mostrarEstadoCamara("Espera a que la cámara enfoque antes de capturar.", true);
      return;
    }
    var canvas = document.getElementById("camara-canvas");
    canvas.width = viewfinder.videoWidth;
    canvas.height = viewfinder.videoHeight;
    canvas.getContext("2d").drawImage(viewfinder, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(function (blob) {
      if (blob) enviarImagen(blob);
    }, "image/jpeg", 0.85);
  }

  function elegirImagen(event) {
    var files = event.target.files;
    if (files && files[0]) {
      detenerCamara();
      enviarImagen(files[0]);
    }
  }

  function inicializarCamara() {
    document.getElementById("activar-camara").addEventListener("click", activarCamara);
    document.getElementById("capturar-imagen").addEventListener("click", capturarImagen);
    document.getElementById("detener-camara").addEventListener("click", detenerCamara);
    document.getElementById("imagen-galeria").addEventListener("change", elegirImagen);
    window.addEventListener("pagehide", detenerCamara);
  }

  inicializarCamara();

  var token = extraerToken();
  if (!token) {
    mostrarError("Link inválido. Pide uno nuevo por WhatsApp.");
    return;
  }

  fetch("/api/v1/panel/" + encodeURIComponent(token))
    .then(function (resp) {
      if (resp.status === 401) {
        mostrarError("Este link venció o no es válido. Pide uno nuevo por WhatsApp.");
        return null;
      }
      if (resp.status === 404) {
        mostrarError("No hay datos registrados para este link.");
        return null;
      }
      if (!resp.ok) {
        throw new Error("http_" + resp.status);
      }
      return resp.json();
    })
    .then(function (resumen) {
      if (resumen) renderizarResumen(resumen);
    })
    .catch(function () {
      // Fetch solo rechaza por falla de red (sin señal): un error HTTP ya
      // se manejo arriba sin llegar aca. El Service Worker intento servir
      // la ultima copia cacheada antes de esto (ver sw.js); si igual fallo,
      // no hay nada guardado para este link.
      document.getElementById("estado-offline").style.display = "block";
      mostrarError("No pude cargar tu resumen sin conexión. Intenta de nuevo cuando tengas señal.");
    });
})();
