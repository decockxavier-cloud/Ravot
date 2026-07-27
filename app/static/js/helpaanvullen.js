// Zelf-curatie fase 1 — micro-vragen op de fiche.
// Eén tik bevestigt of ontkent een voorziening. De knop POST't naar de server;
// bij succes tonen we meteen een bedankje en verbergen we de beantwoorde vraag,
// zodat de bijdrage voelbaar effect heeft. Alles extern (CSP: geen inline JS).
(function () {
  function bedankje(vraag, tekst) {
    var klaar = document.createElement("p");
    klaar.className = "help-klaar";
    klaar.textContent = "✓ " + tekst;
    vraag.replaceWith(klaar);
  }

  document.addEventListener("click", function (e) {
    var knop = e.target.closest(".help-knop");
    if (!knop) return;
    var url = knop.getAttribute("data-stem-url");
    if (!url) return;
    var vraag = knop.closest(".help-vraag");
    var label = vraag ? vraag.querySelector(".help-label") : null;
    var naam = label ? label.textContent.replace(/\?$/, "") : "deze info";
    var jaKnop = knop.classList.contains("help-nee") === false;

    // dubbelklik voorkomen
    var knoppen = vraag ? vraag.querySelectorAll(".help-knop") : [knop];
    knoppen.forEach(function (k) { k.disabled = true; });

    var meta = document.querySelector('meta[name="csrf-token"]');
    var token = meta ? meta.getAttribute("content") : "";

    fetch(url, {
      method: "POST",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": token,
      },
      credentials: "same-origin",
    }).then(function (r) {
      if (!r.ok) throw new Error("stem mislukt");
      if (vraag) {
        bedankje(vraag, jaKnop
          ? "Bedankt! " + naam + " toegevoegd."
          : "Bedankt, genoteerd.");
      }
    }).catch(function () {
      knoppen.forEach(function (k) { k.disabled = false; });
    });
  });
})();
