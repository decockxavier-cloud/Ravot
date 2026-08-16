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

  // Richtingspijltjes op het tracé (patch 256): een lus zonder richting laat
  // je twijfelen welke kant je op moet bij het startpunt.
  (function pijlen() {
    var punten = data.route;
    var stap = Math.max(1, Math.floor(punten.length / 12));
    for (var i = stap; i < punten.length - 1; i += stap) {
      var a = punten[i - 1], b = punten[i];
      var hoek = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
      L.marker(b, {
        interactive: false,
        icon: L.divIcon({
          className: "route-pijl",
          html: '<span style="transform:rotate(' + (90 - hoek) + 'deg)">➤</span>',
          iconSize: [16, 16],
        }),
      }).addTo(kaart);
    }
  })();

  // "Waar ben ik?" (patch 256): ouders willen tijdens het fietsen zien waar ze
  // op de lus zitten. Blijft volledig in de browser — de positie gaat nooit
  // naar de server.
  (function positie() {
    var knop = document.getElementById("mijn-positie");
    if (!knop || !navigator.geolocation) {
      if (knop) knop.hidden = true;
      return;
    }
    var stip = null, kring = null, kijken = null;
    function toon(pos) {
      var p = [pos.coords.latitude, pos.coords.longitude];
      if (!stip) {
        stip = L.circleMarker(p, { radius: 7, color: "#fff", weight: 2,
                                   fillColor: "#2E7D46", fillOpacity: 1 }).addTo(kaart);
        kring = L.circle(p, { radius: pos.coords.accuracy || 30, color: "#2E7D46",
                              weight: 1, fillOpacity: 0.10 }).addTo(kaart);
        kaart.setView(p, Math.max(kaart.getZoom(), 15));
      } else {
        stip.setLatLng(p);
        kring.setLatLng(p).setRadius(pos.coords.accuracy || 30);
      }
      knop.textContent = "📍 Volgen aan";
      knop.classList.add("aan");
    }
    function mis() {
      knop.textContent = "📍 Positie niet gelukt";
      knop.disabled = true;
    }
    knop.addEventListener("click", function () {
      if (kijken !== null) {
        navigator.geolocation.clearWatch(kijken);
        kijken = null;
        if (stip) { kaart.removeLayer(stip); kaart.removeLayer(kring); stip = null; }
        knop.textContent = "📍 Waar ben ik?";
        knop.classList.remove("aan");
        return;
      }
      knop.textContent = "📍 Zoeken…";
      kijken = navigator.geolocation.watchPosition(toon, mis, {
        enableHighAccuracy: true, maximumAge: 5000, timeout: 20000,
      });
    });
  })();

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
