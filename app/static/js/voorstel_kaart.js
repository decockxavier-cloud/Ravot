/* Tracé van een routevoorstel op kaart (patch 201). */
(function () {
  var el = document.getElementById("voorstel-kaart");
  if (!el || typeof L === "undefined") return;
  var punten = JSON.parse(el.dataset.geometrie || "[]");
  if (punten.length < 2) return;
  var kaart = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap" }).addTo(kaart);
  var lijn = L.polyline(punten, { color: "#EE8035", weight: 4 }).addTo(kaart);
  L.marker(punten[0]).addTo(kaart).bindPopup("Start / aankomst");
  kaart.fitBounds(lijn.getBounds(), { padding: [24, 24] });
})();
