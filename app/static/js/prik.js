// Prik-de-plek-kaartje op het toevoegformulier.
// Extern bestand: de CSP laat bewust geen inline scripts toe.
(function () {
  var el = document.getElementById("prik-kaart");
  if (!el || typeof L === "undefined") return;
  var kaart = L.map("prik-kaart").setView([50.85, 4.0], 8);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap" }).addTo(kaart);
  var marker = null,
      lat = document.getElementById("prik-lat"),
      lng = document.getElementById("prik-lng"),
      status = document.getElementById("prik-status");
  function zet(ll, zoom) {
    if (marker) { marker.setLatLng(ll); }
    else {
      marker = L.marker(ll, { draggable: true }).addTo(kaart);
      marker.on("dragend", function () { zet(marker.getLatLng()); });
    }
    lat.value = ll.lat.toFixed(6);
    lng.value = ll.lng.toFixed(6);
    status.textContent = "✅ speld gezet — versleep gerust om te verfijnen";
    if (zoom) kaart.setView(ll, Math.max(kaart.getZoom(), 15));
  }
  kaart.on("click", function (e) { zet(e.latlng); });
  var knop = document.getElementById("prik-hier");
  if (knop) knop.addEventListener("click", function () {
    if (!navigator.geolocation) return;
    status.textContent = "locatie ophalen…";
    navigator.geolocation.getCurrentPosition(function (pos) {
      zet(L.latLng(pos.coords.latitude, pos.coords.longitude), true);
    }, function () {
      status.textContent = "locatie niet beschikbaar — tik op de kaart";
    });
  });
})();
