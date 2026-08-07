/* Printknop fietsbingo (patch 204): inline onclick mag niet van de CSP,
   dus de handler hangt hier extern aan de knop. */
(function () {
  var knop = document.getElementById("bingo-print");
  if (knop) knop.addEventListener("click", function () { window.print(); });
})();
