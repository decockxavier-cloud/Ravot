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
