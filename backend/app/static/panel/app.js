/**
 * Cliente del panel del agricultor (C3).
 *
 * El shell HTML es el mismo para cualquier link: el token vive en la URL
 * (/panel/{token}) y este script lo lee para pedir el resumen y sus precios.
 * No hay login ni sesión: el token en la URL ES la única credencial.
 */
(function () {
  "use strict";

  var graficoPrecios = null;

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

  function mostrarOffline() {
    document.getElementById("estado-offline").style.display = "block";
  }

  function cargarJson(url) {
    return fetch(url).then(function (resp) {
      if (!resp.ok) {
        var error = new Error("http_" + resp.status);
        error.status = resp.status;
        throw error;
      }
      return resp.json();
    });
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

  function mostrarErrorResumen(mensaje) {
    document.getElementById("contenido").innerHTML =
      '<p class="muted">' + escaparHtml(mensaje) + "</p>";
  }

  function mostrarErrorPrecios(mensaje) {
    document.getElementById("estado-precios").textContent = mensaje;
  }

  function formatearPrecio(valor) {
    return new Intl.NumberFormat("es-CL", { maximumFractionDigits: 2 }).format(valor);
  }

  function renderizarTablaPrecios(series) {
    var lista = document.getElementById("detalle-precios");
    var filas = [];
    series.forEach(function (serie) {
      serie.precios.forEach(function (punto) {
        filas.push(
          "<li><strong>" +
            escaparHtml(serie.cultivo) +
            "</strong>: " +
            escaparHtml(formatearPrecio(punto.precio)) +
            " — " +
            escaparHtml(punto.fecha) +
            " (" +
            escaparHtml(serie.unidad) +
            ")</li>",
        );
      });
    });
    lista.innerHTML = filas.length
      ? '<ul class="price-list">' + filas.join("") + "</ul>"
      : '<p class="muted">No hay registros dentro de las últimas 4 semanas.</p>';
  }

  function renderizarPrecios(historial) {
    var estado = document.getElementById("estado-precios");
    var series = (historial.cultivos || []).filter(function (serie) {
      return serie.precios && serie.precios.length;
    });
    if (!series.length) {
      estado.textContent = "No hay registros de precios dentro de las últimas 4 semanas.";
      document.getElementById("detalle-precios").innerHTML = "";
      return;
    }

    var fechas = [];
    series.forEach(function (serie) {
      serie.precios.forEach(function (punto) {
        if (fechas.indexOf(punto.fecha) === -1) fechas.push(punto.fecha);
      });
    });
    fechas.sort();
    estado.textContent = "Datos ODEPA del " + historial.desde + " al " + historial.hasta + ".";
    renderizarTablaPrecios(series);

    var canvas = document.getElementById("grafico-precios");
    if (!window.Chart) {
      estado.textContent += " Gráfico no disponible en este navegador.";
      return;
    }
    if (graficoPrecios) graficoPrecios.destroy();

    var colores = ["#4f7d5a", "#c77c30", "#3c6e91", "#925c85", "#5c677d"];
    graficoPrecios = new window.Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: fechas,
        datasets: series.map(function (serie, indice) {
          return {
            label: serie.cultivo + " (" + serie.unidad + ")",
            data: fechas.map(function (fecha) {
              var punto = serie.precios.find(function (item) {
                return item.fecha === fecha;
              });
              return punto ? punto.precio : null;
            }),
            borderColor: colores[indice % colores.length],
            backgroundColor: colores[indice % colores.length],
            spanGaps: true,
            tension: 0.2,
            pointRadius: 3,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: function (contexto) {
                return contexto.dataset.label + ": " + formatearPrecio(contexto.parsed.y);
              },
            },
          },
        },
        scales: {
          y: { beginAtZero: false, ticks: { callback: formatearPrecio } },
          x: { ticks: { maxRotation: 45, minRotation: 45, autoSkip: true } },
        },
      },
    });
  }

  function cargarResumen(token) {
    cargarJson("/api/v1/panel/" + encodeURIComponent(token))
      .then(renderizarResumen)
      .catch(function (error) {
        if (error.status === 401) {
          mostrarErrorResumen("Este link venció o no es válido. Pide uno nuevo por WhatsApp.");
        } else if (error.status === 404) {
          mostrarErrorResumen("No hay datos registrados para este link.");
        } else {
          mostrarOffline();
          mostrarErrorResumen("No pude cargar tu resumen sin conexión. Intenta de nuevo cuando tengas señal.");
        }
      });
  }

  function cargarPrecios(token) {
    cargarJson("/api/v1/panel/" + encodeURIComponent(token) + "/prices")
      .then(renderizarPrecios)
      .catch(function (error) {
        if (error.status === 401) {
          mostrarErrorPrecios("Este link venció o no es válido.");
        } else if (error.status === 404) {
          mostrarErrorPrecios("No hay datos de precios registrados para este link.");
        } else {
          mostrarOffline();
          mostrarErrorPrecios("No pude cargar los precios sin conexión. Intenta de nuevo cuando tengas señal.");
        }
      });
  }

  var token = extraerToken();
  if (!token) {
    mostrarErrorResumen("Link inválido. Pide uno nuevo por WhatsApp.");
    mostrarErrorPrecios("Link inválido. Pide uno nuevo por WhatsApp.");
    return;
  }

  cargarResumen(token);
  cargarPrecios(token);
})();
