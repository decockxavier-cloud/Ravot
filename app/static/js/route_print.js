/* Printknop routefiche (patch 214) — extern wegens CSP. */
(function () {
  var knop = document.getElementById("route-print");
  if (knop) knop.addEventListener("click", function () { window.print(); });
})();
