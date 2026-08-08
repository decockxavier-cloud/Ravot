/* Kassasamenvatting volgt de gekozen formule (patch 213). Extern script
   wegens CSP; zonder JS toont de samenvatting gewoon de standaardkeuze. */
(function () {
  var blok = document.getElementById("partner-samenvatting");
  if (!blok) return;
  var naam = document.getElementById("ps-naam");
  var prijs = document.getElementById("ps-prijs");
  var incl = document.getElementById("ps-incl");
  var radios = document.querySelectorAll('input[type="radio"][name="plan"]');
  function toon() {
    for (var i = 0; i < radios.length; i++) {
      if (radios[i].checked) {
        naam.textContent = radios[i].dataset.naam || "";
        prijs.textContent = radios[i].dataset.prijs || "";
        incl.textContent = radios[i].dataset.incl || "";
        return;
      }
    }
    naam.textContent = "Kies hierboven een formule";
    prijs.textContent = "—";
    incl.textContent = "—";
  }
  for (var i = 0; i < radios.length; i++) {
    radios[i].addEventListener("change", toon);
  }
  toon();
})();
