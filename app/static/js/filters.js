// Filter-selects navigeren naar de gekozen URL. Extern bestand omdat de CSP
// geen inline onchange toelaat — dat was de reden dat de soort-filter
// (o.a. "Kindvriendelijke horeca") niets deed.
(function () {
  document.querySelectorAll("select[data-nav]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (this.value) window.location.href = this.value;
    });
  });
})();

// Filterkliks springen niet meer naar boven: onthoud de scrollpositie per pad
// en zet ze na de herlaad terug. (Externe file: CSP laat geen inline JS toe.)
(function () {
  var SLEUTEL = "ravot-filterscroll";
  function bewaar() {
    try {
      sessionStorage.setItem(SLEUTEL, JSON.stringify({
        pad: window.location.pathname, y: window.scrollY }));
    } catch (e) { /* private mode e.d.: gewoon niets doen */ }
  }
  document.addEventListener("click", function (e) {
    var chip = e.target.closest("a.filterchip");
    if (chip) bewaar();
  });
  document.querySelectorAll("select[data-nav]").forEach(function (sel) {
    sel.addEventListener("change", bewaar);
  });
  try {
    var vorig = JSON.parse(sessionStorage.getItem(SLEUTEL) || "null");
    sessionStorage.removeItem(SLEUTEL);
    if (vorig && vorig.pad === window.location.pathname && vorig.y > 0) {
      window.scrollTo(0, vorig.y);
    }
  } catch (e) { /* geen herstel */ }
})();
