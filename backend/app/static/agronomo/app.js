/**
 * Cliente de la console de agronomos PRODESAL (C4).
 *
 * El shell HTML es el mismo para cualquier link: el token vive en la URL
 * (/agronomo/{token}) y este script lo lee para pedir el resumen del grupo
 * a la API. Sin login ni sesion: el token en la URL ES la unica credencial.
 */
(function () {
  "use strict";

  function extraerToken() {
    var partes = window.location.pathname.split("/").filter(Boolean);
    // partes = ["agronomo", "<token>"]
    return partes.length >= 2 ? partes[1] : "";
  }

  function escaparHtml(texto) {
    var div = document.createElement("div");
    div.textContent = texto;
    return div.innerHTML;
  }

  function renderizarFila(productor) {
    var cultivos = productor.cultivos && productor.cultivos.length ? productor.cultivos.map(escaparHtml).join(", ") : "—";
    var parcelas = productor.parcelas && productor.parcelas.length
      ? productor.parcelas
          .map(function (p) {
            return escaparHtml(p.cultivo) + " (" + escaparHtml(String(p.superficie_ha)) + " ha)";
          })
          .join(", ")
      : "—";
    return (
      "<tr><td>" +
      escaparHtml(productor.comuna || "—") +
      "</td><td>" +
      escaparHtml(productor.localidad || "—") +
      "</td><td>" +
      cultivos +
      "</td><td>" +
      parcelas +
      "</td></tr>"
    );
  }

  function renderizarGrupo(datos) {
    var contenido = document.getElementById("contenido");
    if (!datos.productores || !datos.productores.length) {
      contenido.innerHTML = '<p class="muted">Este grupo todavía no tiene productores registrados.</p>';
      return;
    }
    var filas = datos.productores.map(renderizarFila).join("");
    contenido.innerHTML =
      '<div class="card"><h2>' +
      escaparHtml(datos.group_label) +
      " — " +
      datos.productores.length +
      ' productor(es)</h2>' +
      "<table><thead><tr><th>Comuna</th><th>Localidad</th><th>Cultivos</th><th>Parcelas</th></tr></thead><tbody>" +
      filas +
      "</tbody></table></div>";
  }

  function mostrarError(mensaje) {
    document.getElementById("contenido").innerHTML = '<p class="muted">' + escaparHtml(mensaje) + "</p>";
  }

  var token = extraerToken();
  if (!token) {
    mostrarError("Link inválido. Pide uno nuevo al equipo de AgroVoz.");
    return;
  }

  fetch("/api/v1/agronomo/" + encodeURIComponent(token))
    .then(function (resp) {
      if (resp.status === 401) {
        mostrarError("Este link venció o no es válido. Pide uno nuevo al equipo de AgroVoz.");
        return null;
      }
      if (resp.status === 404) {
        mostrarError("No hay productores registrados para este grupo.");
        return null;
      }
      if (!resp.ok) {
        throw new Error("http_" + resp.status);
      }
      return resp.json();
    })
    .then(function (datos) {
      if (datos) renderizarGrupo(datos);
    })
    .catch(function () {
      mostrarError("No pude cargar el resumen del grupo. Intenta de nuevo cuando tengas señal.");
    });
})();
