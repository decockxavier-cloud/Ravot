// Routekaart (patch 160): tekent de gezinslus in Ravot-oranje met start/eind
// en de "leuk onderweg"-markers; partners krijgen de grotere ster.
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

  (data.markers || []).forEach(function (m) {
    var icoon = L.divIcon({
      className: "",
      html: m.partner ? "<span style='font-size:22px'>⭐</span>"
                      : "<span style='font-size:17px'>📍</span>",
      iconSize: [24, 24],
    });
    L.marker([m.lat, m.lng], { icon: icoon, title: m.title })
      .addTo(kaart)
      .bindPopup("<strong>" + m.title + "</strong><br>km " + m.km +
                 " · <a href='/e/" + m.slug + "'>bekijk de fiche</a>");
  });
})();
