/**
 * Service Worker minimo viable para el admin de AgroVoz.
 *
 * Cachea UNICAMENTE el shell estatico (HTML base, favicon, HTMX, registro SW).
 * NUNCA cachea datos sensibles: consultas, precios, metricas, exports,
 * ni endpoints dinamicos del dashboard.
 */
const CACHE_NAME = "agrovoz-admin-v1";
const SHELL_ASSETS = [
  "/admin/",
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
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Cache-first SOLO para el shell estatico del admin.
  if (
    SHELL_ASSETS.includes(url.pathname) ||
    url.pathname.startsWith("/static/")
  ) {
    event.respondWith(
      caches.match(event.request).then((response) => {
        return response || fetch(event.request);
      }),
    );
    return;
  }

  // Network-first para TODO lo demas: nunca cacheamos datos dinamicos.
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request)),
  );
});
