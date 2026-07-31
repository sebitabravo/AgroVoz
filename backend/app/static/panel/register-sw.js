/**
 * Registro del Service Worker del panel del agricultor (C3).
 *
 * Se carga al final de <body> en el shell HTML. El SW cachea el shell
 * estatico y el ultimo resumen obtenido, para que el panel funcione sin
 * señal en el campo.
 */
if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("/panel/sw.js", { scope: "/panel/" })
    .catch(() => {
      // Sin SW el panel sigue funcionando online; solo se pierde el modo offline.
    });
}
