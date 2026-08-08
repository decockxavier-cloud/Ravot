// Routekaart (patch 160+162): routelijn + "leuk onderweg"-markers met het
// eigen type-emoji, filterbaar via de gekende groepen Beleven/Ravotten/Smullen.
(function () {
  "use strict";
  var el = document.getElementById("route-kaart");
  var blok = document.getElementById("route-data");
  if (!el || !blok || typeof L === "undefined") return;
  var data = JSON.parse(blok.textContent);
  if (!data.route || !data.route.length) { el.hidden = true; return; }

  var kaart = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap-bijdragers", maxZoom: 18,
  }).addTo(kaart);

  var lijn = L.polyline(data.route, {
    color: "#EE8035", weight: 5, opacity: 0.92, lineJoin: "round",
  }).addTo(kaart);
  L.polyline(data.route, {
    color: "#ffffff", weight: 1.6, opacity: 0.9, dashArray: "6 10",
  }).addTo(kaart);
  kaart.fitBounds(lijn.getBounds(), { padding: [24, 24] });

  if (data.start) {
    L.marker(data.start, {
      icon: L.divIcon({ className: "", html: data.lus ? "🔄" : "▶️",
                        iconSize: [24, 24] }),
      title: "Startpunt",
    }).addTo(kaart);
  }

  // Eén Leaflet-laag per groep, zodat de filters gewoon lagen tonen/verbergen.
  var lagen = { beleven: L.layerGroup(), ravotten: L.layerGroup(),
                smullen: L.layerGroup() };
  (data.markers || []).forEach(function (m) {
    var emoji = m.emoji || "📍";
    // Gekleurde bolletjes per groep (patch 212): losse emoji's op een drukke
    // fietskaart vielen weg. Smullen krijgt een eigen, opvallende kleur.
    var klas = "rv-stip rv-stip-" + (m.groep || "ravotten") +
               (m.partner ? " rv-stip-partner" : "");
    var html = '<span class="' + klas + '">' + emoji +
               (m.partner ? '<span class="rv-stip-ster">⭐</span>' : "") + "</span>";
    var marker = L.marker([m.lat, m.lng], {
      icon: L.divIcon({ className: "", html: html, iconSize: [30, 30] }),
      title: m.title,
    }).bindPopup("<strong>" + m.title + "</strong><br>km " + m.km +
                 " · <a href='/e/" + m.slug + "'>bekijk de fiche</a>");
    (lagen[m.groep] || lagen.ravotten).addLayer(marker);
  });
  Object.keys(lagen).forEach(function (g) { lagen[g].addTo(kaart); });

  // Filterknoppen: "Alles" of exact één groep.
  var knoppen = document.querySelectorAll(".kaart-filter");
  knoppen.forEach(function (b) {
    b.addEventListener("click", function () {
      knoppen.forEach(function (x) { x.classList.toggle("aan", x === b); });
      var keuze = b.dataset.groep;
      Object.keys(lagen).forEach(function (g) {
        if (keuze === "alles" || keuze === g) kaart.addLayer(lagen[g]);
        else kaart.removeLayer(lagen[g]);
      });
    });
  });
})();
