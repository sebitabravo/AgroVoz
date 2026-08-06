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


  var visionStream = null;

  function establecerEstadoCamara(mensaje) {
    var estado = document.getElementById("estado-camara");
    if (estado) estado.textContent = mensaje;
  }

  function detenerCamara() {
    if (!visionStream) return;
    visionStream.getTracks().forEach(function (track) {
      track.stop();
    });
    visionStream = null;
    var video = document.getElementById("visor-camara");
    if (video) video.srcObject = null;
    var capturar = document.getElementById("capturar-imagen");
    if (capturar) capturar.disabled = true;
  }

  function mostrarResultadoImagen(resultado) {
    var imagen = document.getElementById("imagen-anotada");
    if (!imagen || !resultado) return;

    var fuenteImagen =
      resultado.imagen_anotada ||
      resultado.imagen_anotada_url ||
      resultado.annotated_image ||
      resultado.annotated_image_url ||
      "";
    if (resultado.imagen_anotada_base64) {
      fuenteImagen = "data:image/jpeg;base64," + resultado.imagen_anotada_base64;
    }

    // Solo se muestran recursos del propio backend, data URLs de la captura
    // o HTTPS; una respuesta externa no debe convertirse en un canal de XSS.
    var recursoPermitido =
      typeof fuenteImagen === "string" &&
      (fuenteImagen.indexOf("data:image/") === 0 ||
        (fuenteImagen.indexOf("/") === 0 && fuenteImagen.indexOf("//") !== 0) ||
        fuenteImagen.indexOf("https://") === 0);
    if (!recursoPermitido) return;
    imagen.src = fuenteImagen;
    imagen.hidden = false;
    document.getElementById("estado-visor").hidden = true;
  }

  function formatearConfianza(valor) {
    var confianza = Number(valor);
    if (!Number.isFinite(confianza)) return "No disponible";
    if (confianza >= 0 && confianza <= 1) confianza *= 100;
    confianza = Math.max(0, Math.min(100, confianza));
    return confianza.toFixed(1) + "%";
  }

  function mostrarResultadoVision(resultado) {
    var contenedor = document.getElementById("resultado-vision");
    var enfermedad = document.getElementById("vision-enfermedad");
    var confianza = document.getElementById("vision-confianza");
    var fuente = document.getElementById("vision-fuente");
    var fecha = document.getElementById("vision-fecha");
    if (!contenedor || !enfermedad || !confianza || !fuente || !fecha || !resultado) return;

    var nombre =
      resultado.nombre_enfermedad ||
      resultado.enfermedad ||
      resultado.clasificacion ||
      resultado.classification ||
      "No identificada";
    var valorConfianza = Number(resultado.confianza);
    var tieneConfianza = Number.isFinite(valorConfianza);
    enfermedad.textContent = nombre;
    confianza.textContent = formatearConfianza(resultado.confianza);
    fuente.textContent =
      resultado.fuente_inia || resultado.fuente || "No hay una regla INIA vigente para esta identificación.";
    fecha.textContent = resultado.fecha_fuente
      ? "Fuente verificada el " + resultado.fecha_fuente + "."
      : "";

    var fuenteUrl = resultado.fuente_url;
    if (typeof fuenteUrl === "string" && fuenteUrl.indexOf("https://") === 0) {
      var enlace = document.createElement("a");
      enlace.href = fuenteUrl;
      enlace.target = "_blank";
      enlace.rel = "noopener noreferrer";
      enlace.textContent = fuente.textContent;
      fuente.textContent = "";
      fuente.appendChild(enlace);
    }

    contenedor.hidden = false;
    if (resultado.identificado === false || (tieneConfianza && valorConfianza < 0.8)) {
      establecerEstadoCamara(
        resultado.mensaje ||
          "No pude identificar la plaga con suficiente confianza. Prueba con una foto más nítida.",
      );
      return;
    }
    establecerEstadoCamara("Identificación recibida.");
  }

  function enviarImagen(blob) {
    var datos = new FormData();
    datos.append("image", blob, "captura.jpg");
    establecerEstadoCamara("Analizando la imagen…");
    fetch("/api/v1/vision/identify?token=" + encodeURIComponent(token), {
      method: "POST",
      body: datos,
    })
      .then(function (respuesta) {
        if (!respuesta.ok) {
          var error = new Error("http_" + respuesta.status);
          error.status = respuesta.status;
          throw error;
        }
        return respuesta.json();
      })
      .then(function (resultado) {
        mostrarResultadoImagen(resultado);
        mostrarResultadoVision(resultado);
      })
      .catch(function (error) {
        if (error.status === 503) {
          establecerEstadoCamara("La identificación visual todavía no está habilitada.");
          return;
        }
        establecerEstadoCamara("No pude analizar la imagen. Intenta de nuevo cuando tengas señal.");
      });
  }

  function capturarImagen() {
    var video = document.getElementById("visor-camara");
    var canvas = document.getElementById("captura-camara");
    if (!video || !canvas || !visionStream || !video.videoWidth || !video.videoHeight) {
      establecerEstadoCamara("La cámara todavía no está lista. Espera un momento e inténtalo otra vez.");
      return;
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    var contexto = canvas.getContext("2d");
    if (!contexto) {
      establecerEstadoCamara("No pude preparar la captura en este dispositivo.");
      return;
    }
    contexto.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(function (blob) {
      if (!blob) {
        establecerEstadoCamara("No pude preparar la imagen. Intenta de nuevo.");
        return;
      }
      detenerCamara();
      document.getElementById("visor-camara").hidden = true;
      document.getElementById("estado-visor").hidden = false;
      enviarImagen(blob);
    }, "image/jpeg", 0.85);
  }

  function activarCamara() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      establecerEstadoCamara("La cámara necesita un navegador compatible y una conexión HTTPS.");
      return;
    }
    detenerCamara();
    establecerEstadoCamara("Solicitando permiso para usar la cámara…");
    navigator.mediaDevices
      .getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      })
      .then(function (stream) {
        visionStream = stream;
        var video = document.getElementById("visor-camara");
        video.srcObject = stream;
        video.hidden = false;
        document.getElementById("estado-visor").hidden = true;
        document.getElementById("capturar-imagen").disabled = false;
        establecerEstadoCamara("Cámara activa. Centra la hoja y captura la imagen.");
      })
      .catch(function (error) {
        var mensaje = "No pude acceder a la cámara.";
        if (error && error.name === "NotAllowedError") {
          mensaje = "Permiso de cámara denegado. Habilítalo en el navegador para continuar.";
        }
        establecerEstadoCamara(mensaje);
      });
  }

  function inicializarVision() {
    var activar = document.getElementById("activar-camara");
    var capturar = document.getElementById("capturar-imagen");
    if (!activar || !capturar) return;
    activar.addEventListener("click", activarCamara);
    capturar.addEventListener("click", capturarImagen);
    window.addEventListener("pagehide", detenerCamara);
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

  inicializarVision();
  cargarResumen(token);
  cargarPrecios(token);

})();
