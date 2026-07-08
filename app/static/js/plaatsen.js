/* Autocomplete voor stad/postcode. Werkt op elk input met data-plaatsen.
   - vult een <datalist> met canonieke suggesties ("Gent (9000)")
   - bij keuze: postcode/gemeente netjes splitsen naar gekoppelde velden
     (data-vul-postcode / data-vul-gemeente = selector van het doelveld) */
(function () {
  "use strict";
  var velden = document.querySelectorAll("input[data-plaatsen]");
  if (!velden.length) return;

  velden.forEach(function (veld, i) {
    var lijst = document.createElement("datalist");
    lijst.id = "plaatsen-" + i;
    document.body.appendChild(lijst);
    veld.setAttribute("list", lijst.id);
    veld.setAttribute("autocomplete", "off");

    var timer = null, laatste = "";
    veld.addEventListener("input", function () {
      var q = veld.value.trim();
      // Gekozen uit de lijst? ("Naam (9999)") -> splitsen en klaar.
      var m = q.match(/^(.+) \((\d{4})\)$/);
      if (m) {
        var pc = document.querySelector(veld.dataset.vulPostcode || "");
        var gm = document.querySelector(veld.dataset.vulGemeente || "");
        if (pc) pc.value = m[2];
        if (gm) gm.value = m[1];
        if (veld.dataset.vulPostcode && veld.dataset.zelf === "gemeente") veld.value = m[1];
        if (veld.dataset.zelf === "postcode") veld.value = m[2];
        return;
      }
      if (q.length < 2 || q === laatste) return;
      clearTimeout(timer);
      timer = setTimeout(function () {
        laatste = q;
        fetch("/api/plaatsen?q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            lijst.innerHTML = "";
            (d.suggesties || []).forEach(function (s) {
              var opt = document.createElement("option");
              opt.value = s.label;
              lijst.appendChild(opt);
            });
          })
          .catch(function () { /* stil falen: veld blijft gewoon werken */ });
      }, 180);
    });
  });
})();
