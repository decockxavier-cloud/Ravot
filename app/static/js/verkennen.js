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

      function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
          return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
      }

      var fiche = '<div class="kaart-fiche">';
      if (m.img) {
        fiche += '<img class="fiche-img" src="' + esc(m.img) + '" alt="" ' +
                 "onerror=\"this.style.display='none'\">";
      }
      fiche += '<div class="fiche-body">';
      fiche += '<strong class="fiche-titel">' + esc(m.title) + "</strong>";
      var meta = [];
      if (m.gemeente) meta.push("📍 " + esc(m.gemeente));
      if (m.datum) meta.push("🕐 " + esc(m.datum));
      if (meta.length) fiche += '<div class="fiche-meta">' + meta.join(" · ") + "</div>";

      var badges = "";
      if (m.leeftijd) badges += '<span class="fiche-badge">' + esc(m.leeftijd) + "</span>";
      if (m.free) badges += '<span class="fiche-badge groen">Gratis</span>';
      if (m.indoor) badges += '<span class="fiche-badge">🌧️ binnen</span>';
      if (m.score) badges += '<span class="fiche-badge geel">😄 ' + esc(m.score) +
                             (m.count ? " (" + esc(m.count) + ")" : "") + "</span>";
      else badges += '<span class="fiche-badge grijs">nog geen score</span>';
      if (badges) fiche += '<div class="fiche-badges">' + badges + "</div>";

      fiche += '<a class="fiche-knop" href="' + esc(m.url) + '">Bekijk activiteit →</a>';
      fiche += "</div></div>";

      L.circleMarker([m.lat, m.lng],
        { radius: 9, color: kleur, fillColor: kleur, fillOpacity: 0.85 })
        .addTo(map)
        .bindPopup(fiche, { minWidth: 220, maxWidth: 260, className: "fiche-popup" });
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
