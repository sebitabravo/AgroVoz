// Inicializacion de Chart.js para la pagina de Métricas.
// Lee los datos desde un bloque <script type="application/json"> embebido
// por el template (metrics.html), para no necesitar scripts inline y
// mantener el CSP estricto (script-src 'self'). El JSON se construye en
// app/admin/admin.py (metrics_page) y se serializa con json.dumps.
(function () {
  var dataEl = document.getElementById('metrics-data');
  if (!dataEl) return;
  if (typeof Chart === 'undefined') { console.warn('Chart.js no disponible'); return; }

  var data = JSON.parse(dataEl.textContent);

  Chart.defaults.font.family = 'system-ui, sans-serif';
  Chart.defaults.color = '#7e827a';

  var ACCENT = '#4f7d5a', INFO = '#2a5f90', CREDIT = '#6d28d9';

  // ── Diario (line) ──────────────────────────────
  new Chart(document.getElementById('chart-daily'), {
    type: 'line',
    data: {
      labels: data.diario.labels.map(function (d) { return d.slice(5); }),
      datasets: [{
        data: data.diario.counts,
        borderColor: ACCENT,
        backgroundColor: 'rgba(79,125,90,0.12)',
        fill: true, tension: 0.3, borderWidth: 2,
        pointRadius: 2, pointHoverRadius: 5,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
    }
  });

  // ── Intents (doughnut) ─────────────────────────
  new Chart(document.getElementById('chart-intents'), {
    type: 'doughnut',
    data: {
      labels: ['Precio', 'Clima', 'Crédito', 'Desconocido'],
      datasets: [{
        data: [
          data.intents.precio,
          data.intents.clima,
          data.intents.credito,
          data.intents.desconocido,
        ],
        backgroundColor: [ACCENT, INFO, CREDIT, '#c8c7bf'],
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '65%',
      plugins: { legend: { display: false } }
    }
  });

  // ── Productos (bar horizontal) ─────────────────
  var prodCtx = document.getElementById('chart-products');
  if (prodCtx && data.productos) {
    new Chart(prodCtx, {
      type: 'bar',
      data: {
        labels: data.productos.labels,
        datasets: [{
          data: data.productos.counts,
          backgroundColor: ACCENT, borderRadius: 4, barThickness: 16,
        }]
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 } } }
      }
    });
  }
})();
