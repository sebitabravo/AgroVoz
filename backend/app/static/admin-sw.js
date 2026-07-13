/**
 * Service Worker minimo viable para el admin de AgroVoz.
 *
 * Cachea UNICAMENTE assets estaticos del shell (favicon, HTMX, registro SW).
 * NUNCA cachea HTML del dashboard ni endpoints dinamicos: el admin es
 * autenticado y sus paginas contienen datos sensibles que no deben
 * persistir en el navegador (Ley 21.719).
 */
const CACHE_NAME = "agrovoz-admin-v1";
const SHELL_ASSETS = [
  "/static/favicon.svg",
  "/static/htmx.min.js",
  "/static/admin-pwa-register.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Purga caches de versiones anteriores al cambiar CACHE_NAME.
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Cache-first SOLO para assets estaticos. El resto (HTML autenticado,
  // endpoints dinamicos) no se intercepta: va siempre directo a la red.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request)),
    );
  }
});
