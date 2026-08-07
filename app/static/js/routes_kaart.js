/* Overzichtskaart gezinsfietsroutes (patch 198): startpins per route en
   klikbare streeklabels — zo ziet een toerist meteen wáár hij is en wat er
   in de buurt te rijden valt. */
(function () {
  var el = document.getElementById("routes-kaart");
  if (!el || typeof L === "undefined") return;
  var routes = JSON.parse(el.dataset.routes || "[]");
  var regios = JSON.parse(el.dataset.regios || "[]");
  if (!routes.length) return;
  var kaart = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap" }).addTo(kaart);
  var grenzen = [];
  routes.forEach(function (r) {
    grenzen.push([r.lat, r.lng]);
    L.marker([r.lat, r.lng]).addTo(kaart).bindPopup(
      '<strong><a href="' + r.url + '">' + r.titel + "</a></strong><br>" +
      r.km + " km" + (r.regio ? " · " + r.regio : ""));
  });
  regios.forEach(function (g) {
    L.marker([g.lat, g.lng], {
      icon: L.divIcon({ className: "regio-label",
        html: '<a href="' + g.url + '">' + g.regio + "</a>",
        iconSize: null })
    }).addTo(kaart);
  });
  kaart.fitBounds(grenzen, { padding: [40, 40], maxZoom: 11 });
})();
