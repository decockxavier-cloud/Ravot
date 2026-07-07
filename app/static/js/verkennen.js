// Kaartlogica los van de HTML zodat de strikte CSP geen inline script hoeft toe te laten.
(function () {
  function init() {
    var el = document.getElementById("map-data");
    if (!el || typeof L === "undefined") { setTimeout(init, 50); return; }
    var data = JSON.parse(el.textContent);
    var map = L.map("map").setView(data.center, data.zoom || 10);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(map);
    (data.markers || []).forEach(function (m) {
      if (m.lat == null || m.lng == null) return;

      function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
          return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
      }

      // Kleur + icoon op basis van type activiteit
      var kleur, ico;
      if (m.score && m.score >= 4) { kleur = "#2E7D46"; ico = "😄"; }       // goed gescoord
      else if (m.free) { kleur = "#F4B233"; ico = "🎈"; }                    // gratis
      else if (m.indoor) { kleur = "#5B8DEF"; ico = "🏠"; }                  // binnen
      else { kleur = "#EE8035"; ico = "📍"; }                                // overig

      // Custom pin: een druppel met een wit rondje en het icoon erin
      var html = '<div class="rv-pin" style="--pin:' + kleur + '">' +
                 '<span class="rv-pin-ico">' + ico + '</span></div>';
      var icon = L.divIcon({
        className: "rv-pin-wrap",
        html: html,
        iconSize: [34, 44],
        iconAnchor: [17, 44],
        popupAnchor: [0, -40],
      });

      var fiche = '<div class="kaart-fiche">';
      if (m.img) {
        fiche += '<img class="fiche-img" src="' + esc(m.img) + '" alt="" ' +
                 "onerror=\"this.style.display='none'\">";
      }
      fiche += '<div class="fiche-body">';
      fiche += '<strong class="fiche-titel">' + esc(m.title) + "</strong>";
      var meta = [];
      if (m.adres) meta.push("📍 " + esc(m.adres) + (m.gemeente ? ", " + esc(m.gemeente) : ""));
      else if (m.gemeente) meta.push("📍 " + esc(m.gemeente));
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

      L.marker([m.lat, m.lng], { icon: icon })
        .addTo(map)
        .bindPopup(fiche, { minWidth: 250, maxWidth: 300, className: "fiche-popup" });
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
