/* Klein kaartje op de detailpagina: toont waar de plek ligt. Losse JS zodat
   de strikte CSP geen inline script hoeft toe te laten. */
(function () {
  "use strict";
  var el = document.getElementById("minimap");
  if (!el || typeof L === "undefined") return;
  var lat = parseFloat(el.dataset.lat), lng = parseFloat(el.dataset.lng);
  if (isNaN(lat) || isNaN(lng)) return;
  var map = L.map(el, { scrollWheelZoom: false }).setView([lat, lng], 15);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap-bijdragers"
  }).addTo(map);
  L.marker([lat, lng]).addTo(map).bindPopup(el.dataset.titel || "");
})();
