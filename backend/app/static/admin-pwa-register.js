/**
 * Registro del Service Worker para el admin de AgroVoz.
 *
 * Se carga al final de <body> en base.html para no bloquear el render.
 * El SW cachea solo el shell estatico; nunca datos sensibles.
 */
if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("/admin/sw.js", { scope: "/admin/" })
    .then((reg) => console.log("SW registered:", reg.scope))
    .catch((err) => console.error("SW registration failed:", err));
}
