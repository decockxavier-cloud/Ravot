// Kaartlogica los van de HTML zodat de strikte CSP geen inline script hoeft toe te laten.
(function () {
  function init() {
    var el = document.getElementById("map-data");
    if (!el || typeof L === "undefined") { setTimeout(init, 50); return; }
    var data = JSON.parse(el.textContent);
    var map = L.map("map").setView(data.center, data.zoom || 10);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(map);
    var cluster = (typeof L.markerClusterGroup === "function")
      ? L.markerClusterGroup({ maxClusterRadius: 50, spiderfyOnMaxZoom: true,
                               showCoverageOnHover: false, chunkedLoading: true })
      : null;
    var laag = cluster || map;   // val terug op de kaart als de plugin ontbreekt
    (data.markers || []).forEach(function (m) {
      if (m.lat == null || m.lng == null) return;

      function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
          return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
      }

      // Kleur toont score/gratis/binnen; het glyph toont het TYPE (speeltuin,
      // theater, museum…) zodat je op de kaart meteen ziet wat het is.
      var kleur;
      if (m.score && m.score >= 4) { kleur = "#2E7D46"; }        // goed gescoord
      else if (m.free) { kleur = "#F4B233"; }                    // gratis
      else if (m.indoor) { kleur = "#5B8DEF"; }                  // binnen
      else { kleur = "#EE8035"; }                                // overig
      var ico = m.emoji || "📍";

      // Custom pin: een druppel met een wit rondje en het type-icoon erin
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
        .addTo(laag)
        .bindPopup(fiche, { minWidth: 250, maxWidth: 300, className: "fiche-popup" });
    });
    if (cluster) map.addLayer(cluster);
    // Geen automatische fitBounds meer over álle markers: met data in BE, NL én
    // Noord-Frankrijk zou dat de kaart veel te ver uitzoomen en alles op één hoop
    // duwen. We vertrouwen op de door de server gekozen centrering (gezochte
    // plaats, profiel of België) en laten clustering de dichtheid oplossen.

    // "Plekken in mijn buurt": zoom via browser-locatie op de eigen positie.
    var knop = document.getElementById("inbuurt");
    if (knop && navigator.geolocation) {
      knop.addEventListener("click", function () {
        var oud = knop.textContent;
        knop.disabled = true;
        knop.textContent = "📍 Locatie zoeken…";
        navigator.geolocation.getCurrentPosition(function (pos) {
          var lat = pos.coords.latitude, lng = pos.coords.longitude;
          map.setView([lat, lng], 15);
          L.circleMarker([lat, lng], {
            radius: 9, color: "#2e7d32", weight: 3,
            fillColor: "#4caf50", fillOpacity: 0.6
          }).addTo(map).bindPopup("Jouw locatie").openPopup();
          knop.disabled = false;
          knop.textContent = oud;
        }, function () {
          knop.disabled = false;
          knop.textContent = oud;
          alert("We konden je locatie niet ophalen. Sta locatietoegang toe in je browser en probeer opnieuw.");
        }, { enableHighAccuracy: true, timeout: 8000 });
      });
    } else if (knop) {
      knop.style.display = "none";   // geen geolocatie in deze browser
    }
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
