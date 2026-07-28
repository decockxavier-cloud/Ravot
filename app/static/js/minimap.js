/* Klein kaartje op detailpagina's. Losse JS zodat de strikte CSP geen inline
   script hoeft toe te laten. Twee modi:
   - data-lat + data-lng (+ data-titel): één punt (eventpagina)
   - data-markers (JSON-lijst van {lat, lng, title}): meerdere genummerde
     punten met automatische zoom (publieke daguitstap-pagina) */
(function () {
  "use strict";
  var el = document.getElementById("minimap");
  if (!el || typeof L === "undefined") return;

  var tegels = function (map) {
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap-bijdragers"
    }).addTo(map);
  };

  // Meerdere punten (daguitstap): genummerde markers + passende zoom.
  if (el.dataset.markers) {
    var punten = [];
    try { punten = JSON.parse(el.dataset.markers) || []; } catch (e) { punten = []; }
    if (!punten.length) return;
    var map = L.map(el, { scrollWheelZoom: false });
    tegels(map);
    var groep = [];
    punten.forEach(function (p, i) {
      var m = L.marker([p.lat, p.lng]).addTo(map)
        .bindPopup("<strong>" + (i + 1) + ". " + p.title + "</strong>");
      groep.push(m.getLatLng());
    });
    map.fitBounds(L.latLngBounds(groep).pad(0.2));
    return;
  }

  // Eén punt (eventpagina).
  var lat = parseFloat(el.dataset.lat), lng = parseFloat(el.dataset.lng);
  if (isNaN(lat) || isNaN(lng)) return;
  var kaart = L.map(el, { scrollWheelZoom: false }).setView([lat, lng], 15);
  tegels(kaart);
  L.marker([lat, lng]).addTo(kaart).bindPopup(el.dataset.titel || "");
})();
