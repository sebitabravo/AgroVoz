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
