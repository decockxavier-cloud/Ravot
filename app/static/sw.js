// Ravot service worker.
// Strategie: NETWERK EERST voor alles (zodat updates na een deploy meteen
// zichtbaar zijn), met de cache enkel als offline-terugval. Zo kan de
// geinstalleerde PWA nooit meer op een verouderde CSS/JS blijven hangen.
const CACHE = "ravot-v3";
const STATICS = ["/static/css/ravot.css", "/static/js/app.js", "/static/img/icon.svg"];

self.addEventListener("install", (e) => {
  self.skipWaiting();                       // nieuwe versie meteen actief maken
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATICS).catch(() => {})));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((namen) => Promise.all(namen.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())     // ook open tabbladen overnemen
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  // Netwerk eerst; lukt dat, ververs meteen de cache. Faalt het (offline),
  // val terug op de laatst gecachte versie.
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp && resp.status === 200 && resp.type === "basic") {
          const kopie = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, kopie)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
