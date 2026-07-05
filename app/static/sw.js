// Minimale service worker: cache van statics, netwerk eerst voor pagina's
const CACHE = "ravot-v1";
const STATICS = ["/static/css/ravot.css", "/static/js/app.js", "/static/img/icon.svg"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATICS)));
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
