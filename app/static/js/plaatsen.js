/* Autocomplete voor stad/postcode. Werkt op elk input met data-plaatsen.
   Na keuze krijgt het veld de juiste waarde (postcode of naam) en worden
   gekoppelde velden (data-vul-postcode / data-vul-gemeente) mee gevuld.
   Voor de kaart laten we "Naam (9000)" staan: de server gebruikt de postcode,
   zodat dubbelzinnige namen (bv. meerdere 'Beveren') toch juist landen. */
(function () {
  "use strict";
  var velden = document.querySelectorAll("input[data-plaatsen]");
  if (!velden.length) return;

  function kies(sel) {
    if (!sel) return null;
    try { return document.querySelector(sel); } catch (e) { return null; }
  }

  velden.forEach(function (veld, i) {
    var lijst = document.createElement("datalist");
    lijst.id = "plaatsen-" + i;
    document.body.appendChild(lijst);
    veld.setAttribute("list", lijst.id);
    veld.setAttribute("autocomplete", "off");

    var timer = null, laatste = "";

    function verwerkKeuze() {
      var m = veld.value.trim().match(/^(.+?)\s*\((\d{4,5})\)/);
      if (!m) return false;
      var pc = kies(veld.dataset.vulPostcode);
      var gm = kies(veld.dataset.vulGemeente);
      if (pc) pc.value = m[2];
      if (gm) gm.value = m[1];
      if (veld.dataset.zelf === "postcode") veld.value = m[2];
      else if (veld.dataset.zelf === "gemeente" && (pc || gm)) veld.value = m[1];
      return true;
    }

    veld.addEventListener("change", verwerkKeuze);
    veld.addEventListener("input", function () {
      if (verwerkKeuze()) return;
      var q = veld.value.trim();
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
          .catch(function () { /* stil falen */ });
      }, 160);
    });
  });
})();
