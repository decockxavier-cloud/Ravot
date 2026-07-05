// Kaartlogica los van de HTML zodat de strikte CSP geen inline script hoeft toe te laten.
(function () {
  function init() {
    var el = document.getElementById("map-data");
    if (!el || typeof L === "undefined") { setTimeout(init, 50); return; }
    var data = JSON.parse(el.textContent);
    var map = L.map("map").setView(data.center, 10);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(map);
    (data.markers || []).forEach(function (m) {
      if (m.lat == null || m.lng == null) return;
      var kleur = m.free ? "#F2B705" : (m.score && m.score >= 4 ? "#2E7D46" : "#5f6d63");
      L.circleMarker([m.lat, m.lng],
        { radius: 9, color: kleur, fillColor: kleur, fillOpacity: 0.85 })
        .addTo(map)
        .bindPopup('<strong><a href="' + m.url + '">' + m.title + "</a></strong>" +
                   (m.score ? "<br>😄 Ravotscore " + m.score : "") +
                   (m.free ? "<br>Gratis" : ""));
    });
    if (data.markers && data.markers.length) {
      var pts = data.markers.filter(function (m) { return m.lat != null; })
                            .map(function (m) { return [m.lat, m.lng]; });
      if (pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 13 });
    }
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
