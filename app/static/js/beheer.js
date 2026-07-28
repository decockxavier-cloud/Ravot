/* Beheer-scripts (extern wegens strikte CSP: geen inline JS toegelaten).
   Bevat: Markdown-editorknoppen, euro→punten-calculator (beloningen) en
   de AI-voortgangspoller van de horeca-import. Elk blok controleert eerst
   of zijn elementen bestaan, zodat dit bestand veilig op elke beheerpagina
   geladen kan worden. */
(function () {
  "use strict";

  // -- Markdown-editor (pagina's en mailteksten bewerken) --------------------
  var ta = document.getElementById("md-tekst");
  if (ta) {
    var wrap = function (voor, na) {
      var s = ta.selectionStart, e = ta.selectionEnd, tekst = ta.value;
      var sel = tekst.slice(s, e) || "tekst";
      ta.value = tekst.slice(0, s) + voor + sel + na + tekst.slice(e);
      ta.focus();
      ta.selectionStart = s + voor.length;
      ta.selectionEnd = s + voor.length + sel.length;
    };
    var regel = function (prefix) {
      var s = ta.selectionStart, tekst = ta.value;
      var lijnStart = tekst.lastIndexOf("\n", s - 1) + 1;
      ta.value = tekst.slice(0, lijnStart) + prefix + tekst.slice(lijnStart);
      ta.focus();
    };
    document.querySelectorAll(".md-knoppen button").forEach(function (b) {
      b.addEventListener("click", function () {
        var t = b.dataset.md;
        if (t === "bold") wrap("**", "**");
        else if (t === "italic") wrap("*", "*");
        else if (t === "h2") regel("## ");
        else if (t === "ul") regel("- ");
        else if (t === "link") wrap("[", "](https://)");
      });
    });
  }

  // -- Beloningen: euro → punten meerekenen ----------------------------------
  var eur = document.getElementById("bel-eur"), pt = document.getElementById("bel-pt");
  if (eur && pt) {
    var puntEur = parseFloat(pt.dataset.puntEur || "0");
    if (puntEur > 0) {
      eur.addEventListener("input", function () {
        var v = parseFloat(eur.value.replace(",", "."));
        if (!isNaN(v)) pt.value = Math.round(v / puntEur / 10) * 10;
      });
    }
  }

  // -- Horeca-import: AI-voortgang pollen ------------------------------------
  var poll = document.getElementById("ai-poll");
  if (poll && poll.dataset.bezig === "1" && poll.dataset.url) {
    var teller = document.getElementById("ai-teller");
    var vul = document.getElementById("ai-balk-vul");
    var badge = document.getElementById("ai-bezig");
    var timer = setInterval(function () {
      fetch(poll.dataset.url).then(function (r) { return r.json(); }).then(function (d) {
        if (teller) teller.textContent = "AI-advies: " + d.klaar + "/" + d.totaal + " beoordeeld";
        if (vul && d.totaal) vul.style.width = Math.round(d.klaar / d.totaal * 100) + "%";
        if (d.fout && badge) { badge.textContent = "AI-fout: " + d.fout; badge.className = "badge pil-uit"; }
        if (!d.bezig) {
          clearInterval(timer);
          if (badge && !d.fout) badge.textContent = "AI klaar — lijst wordt ververst…";
          // Klaar: het zoekformulier één keer opnieuw indienen zodat de
          // badges en sortering meteen kloppen.
          if (!d.fout) {
            var form = document.querySelector("form.acties") || document.forms[0];
            if (form) form.submit();
          }
        }
      }).catch(function () {});
    }, 2500);
  }
})();
