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

  // Op een telefoon is er geen plaats voor vijftien namen naast elkaar: dan
  // tonen we enkel het aantal in een rond bolletje. Aantikken geeft de naam.
  var smal = el.clientWidth < 520;

  // Streken die op het scherm te dicht bij elkaar vallen, samenvoegen tot één
  // bol met de som. Anders overlappen ze alsnog — precies het probleem dat we
  // wilden oplossen.
  function samengevoegd() {
    var min_px = smal ? 46 : 150;
    var uit = [];
    regios.slice().sort(function (a, b) { return b.aantal - a.aantal; })
      .forEach(function (g) {
        var p = kaart.latLngToContainerPoint([g.lat, g.lng]);
        for (var i = 0; i < uit.length; i++) {
          var q = kaart.latLngToContainerPoint([uit[i].lat, uit[i].lng]);
          if (Math.abs(p.x - q.x) < min_px && Math.abs(p.y - q.y) < 34) {
            uit[i].aantal += g.aantal;
            uit[i].namen.push(g.regio);
            return;
          }
        }
        uit.push({ lat: g.lat, lng: g.lng, aantal: g.aantal,
                   regio: g.regio, namen: [g.regio] });
      });
    return uit;
  }

  function tekenStreken() {
    streeklaag.clearLayers();
    samengevoegd().forEach(maakBel);
  }

  function maakBel(g) {
    var naam = g.regio.split("/")[0].trim();     // "Meetjesland / Gentse rand"
    var bel = L.marker([g.lat, g.lng], {
      icon: L.divIcon({
        className: smal ? "streek-bol" : "streek-bel",
        html: smal ? String(g.aantal)
          : '<span class="streek-naam">' + naam + "</span>" +
            '<span class="streek-tel">' + g.aantal + "</span>",
        iconSize: null,
      }),
      title: g.regio + ": " + g.aantal + " route(s)",
      zIndexOffset: g.aantal,        // grootste aanbod bovenop bij overlap
    }).addTo(streeklaag);
    bel.bindTooltip((g.namen || [g.regio]).join(" · ") + " — " + g.aantal
                    + " route(s)", { direction: "top" });
    bel.on("click", function () {
      // Inzoomen in plaats van meteen wegnavigeren: zo blijft de bezoeker op
      // de kaart en ziet hij de bellen uit elkaar vallen.
      kaart.setView([g.lat, g.lng], Math.min(kaart.getZoom() + 2, DREMPEL + 1));
    });
  }

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
  kaart.on("zoomend", function () { pasNiveauAan(); tekenStreken(); });
  kaart.on("moveend", tekenStreken);

  // Strak op de selectie passen: bij één streek zoomt hij vanzelf diep genoeg
  // in, waardoor de losse routes meteen zichtbaar zijn.
  kaart.fitBounds(grenzen, { padding: [30, 30], maxZoom: 12 });
  tekenStreken();
  pasNiveauAan();
})();
