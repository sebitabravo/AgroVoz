/**
 * Service Worker del panel del agricultor (C3).
 *
 * Cachea el shell estatico (cache-first) y el ULTIMO resumen obtenido para
 * este link especifico (network-first con fallback a cache), para que el
 * panel siga siendo util sin señal en el campo.
 *
 * El resumen es dato personal del propio productor, visto en su propio
 * dispositivo para su propia conveniencia offline: se cachea acotado a la
 * MISMA duracion del link (ver panel_link_ttl_hours en el backend). No
 * persiste indefinidamente ni se comparte con otro origen.
 */
const CACHE_NAME = "agrovoz-panel-v3";
const SHELL_ASSETS = [
  "/static/panel/manifest.json",
  "/static/panel/register-sw.js",
  "/static/panel/app.js",
  "/static/chart.umd.min.js",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

function panelTokenFromPath(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "panel" && parts.length >= 2) return parts[1];
  if (parts[0] === "api" && parts[1] === "v1" && parts[2] === "panel") {
    return parts[3] || null;
  }
  return null;
}

function tokenExpiryMs(pathname) {
  const token = panelTokenFromPath(pathname);
  if (!token) return null;
  const expiresAt = Number(token.split(".")[1]);
  return Number.isSafeInteger(expiresAt) && expiresAt > 0 ? expiresAt * 1000 : null;
}

function isFreshPanelUrl(url, now = Date.now()) {
  const expiry = tokenExpiryMs(url.pathname);
  return expiry !== null && now <= expiry;
}

async function purgePanelToken(token) {
  if (!token) return;
  const cache = await caches.open(CACHE_NAME);
  const requests = await cache.keys();
  await Promise.all(
    requests
      .filter((request) => panelTokenFromPath(new URL(request.url).pathname) === token)
      .map((request) => cache.delete(request)),
  );
}

async function purgeExpiredPanelEntries() {
  const cache = await caches.open(CACHE_NAME);
  const requests = await cache.keys();
  await Promise.all(
    requests
      .filter((request) => {
        const url = new URL(request.url);
        return panelTokenFromPath(url.pathname) && !isFreshPanelUrl(url);
      })
      .map((request) => cache.delete(request)),
  );
}

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
      .then(() => purgeExpiredPanelEntries())
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Shell estatico: cache-first.
  if (url.pathname.startsWith("/static/panel/") || url.pathname.startsWith("/static/icon-")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request)),
    );
    return;
  }

  // Resumen y precios (/api/v1/panel/{token}/prices): network-first, caen a
  // la última copia offline.
  // La clave de cache incluye el token completo: un dispositivo compartido
  // nunca sirve el resumen cacheado de OTRO link.
  if (url.pathname.startsWith("/api/v1/panel/")) {
    if (!isFreshPanelUrl(url)) {
      event.respondWith(
        purgePanelToken(panelTokenFromPath(url.pathname)).then(
          () => new Response("Link vencido o inválido", { status: 401 }),
        ),
      );
      return;
    }
    event.respondWith(
      fetch(event.request)
        .then(async (response) => {
          if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(event.request, response.clone());
          } else if (response.status === 401 || response.status === 403) {
            await purgePanelToken(panelTokenFromPath(url.pathname));
          }
          return response;
        })
        .catch(() => caches.match(event.request)),
    );
    return;
  }

  // Shell HTML del panel (/panel/{token}): cache-first para que abra sin señal.
  if (url.pathname.startsWith("/panel/")) {
    if (!isFreshPanelUrl(url)) {
      event.respondWith(
        purgePanelToken(panelTokenFromPath(url.pathname)).then(
          () => new Response("Link vencido o inválido", { status: 401 }),
        ),
      );
      return;
    }
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then(async (response) => {
          if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(event.request, response.clone());
          }
          return response;
        });
      }),
    );
  }
});
