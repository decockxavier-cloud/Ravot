/* Overzichtskaart gezinsfietsroutes (patch 257).

   Voorheen: één marker per route plus alle streeklabels tegelijk, uitgezoomd
   over half West-Europa. Met 62 routes stapelden de pins tot een blauwe berg
   en lagen de streeknamen over elkaar heen — onleesbaar.

   Nu beweegt het detailniveau mee met de zoom: uitgezoomd zie je één bel per
   streek met het aantal routes erin, ingezoomd de routes zelf. */
(function () {
  "use strict";
  var el = document.getElementById("routes-kaart");
  if (!el || typeof L === "undefined") return;
  var routes = JSON.parse(el.dataset.routes || "[]");
  var regios = JSON.parse(el.dataset.regios || "[]");
  if (!routes.length) return;

  var DREMPEL = 10;              // vanaf deze zoom tonen we losse routes
  var kaart = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap", maxZoom: 18,
  }).addTo(kaart);

  var streeklaag = L.layerGroup().addTo(kaart);
  var routelaag = L.layerGroup();

  regios.forEach(function (g) {
    L.marker([g.lat, g.lng], {
      icon: L.divIcon({
        className: "streek-bel",
        html: '<span class="streek-naam">' + g.regio + "</span>" +
              '<span class="streek-tel">' + g.aantal + "</span>",
        iconSize: null,
      }),
      title: g.regio + ": " + g.aantal + " route(s)",
    }).addTo(streeklaag).on("click", function () {
      // Inzoomen op de streek in plaats van meteen wegnavigeren: zo blijft
      // de bezoeker op de kaart en ziet hij de routes verschijnen.
      kaart.setView([g.lat, g.lng], DREMPEL + 1);
    });
  });

  var grenzen = [];
  routes.forEach(function (r) {
    grenzen.push([r.lat, r.lng]);
    L.marker([r.lat, r.lng], {
      icon: L.divIcon({ className: "route-pin", html: "🚲", iconSize: [26, 26] }),
      title: r.titel,
    }).addTo(routelaag).bindPopup(
      '<strong><a href="' + r.url + '">' + r.titel + "</a></strong><br>" +
      r.km + " km" + (r.regio ? " · " + r.regio : ""));
  });

  function pasNiveauAan() {
    var diep = kaart.getZoom() >= DREMPEL;
    if (diep && !kaart.hasLayer(routelaag)) {
      kaart.addLayer(routelaag);
      kaart.removeLayer(streeklaag);
    } else if (!diep && !kaart.hasLayer(streeklaag)) {
      kaart.addLayer(streeklaag);
      kaart.removeLayer(routelaag);
    }
  }
  kaart.on("zoomend", pasNiveauAan);

  // Strak op de selectie passen: bij één streek zoomt hij vanzelf diep genoeg
  // in, waardoor de losse routes meteen zichtbaar zijn.
  kaart.fitBounds(grenzen, { padding: [30, 30], maxZoom: 12 });
  pasNiveauAan();
})();
