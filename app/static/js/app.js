// PWA: service worker + installatieprompt ("Zet Ravot op je beginscherm")
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(() => {});
}
// Chrome/Android geeft een echte installatieprompt (beforeinstallprompt).
// iOS kent die API niet: daar tonen we een korte uitleg (Deel → beginscherm).
// Al geïnstalleerd (standalone) of eerder weggeklikt: dan tonen we niets.
(function () {
  var rij = document.getElementById("install-rij");
  var cta = document.getElementById("install-cta");
  if (!rij || !cta) return;
  var standalone = window.matchMedia("(display-mode: standalone)").matches ||
                   window.navigator.standalone === true;
  var weggeklikt = false;
  try {
    var tot = parseInt(localStorage.getItem("ravot_installtip_weg") || "0", 10);
    weggeklikt = tot > Date.now();
  } catch (e) { /* private modus: gewoon tonen */ }
  if (standalone || weggeklikt) return;

  var weg = document.getElementById("install-weg");
  if (weg) weg.addEventListener("click", function () {
    rij.hidden = true;
    try {  // 60 dagen niet meer tonen
      localStorage.setItem("ravot_installtip_weg",
                           String(Date.now() + 60 * 24 * 3600 * 1000));
    } catch (e) { /* ok */ }
  });

  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
              (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (isIOS) {
    rij.hidden = false;
    var uitleg = document.getElementById("install-ios-uitleg");
    cta.addEventListener("click", function () {
      if (uitleg) uitleg.hidden = !uitleg.hidden;
    });
    return;
  }
  var deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    rij.hidden = false;
  });
  cta.addEventListener("click", function () {
    rij.hidden = true;
    if (deferredPrompt) { deferredPrompt.prompt(); deferredPrompt = null; }
  });
})();

// Dynamisch kinderen toevoegen (onbeperkt) in onboarding/profiel
document.addEventListener("click", function (e) {
  var btn = e.target.closest("[data-add-kind]");
  if (!btn) return;
  e.preventDefault();
  var wrap = document.getElementById("kinderen");
  if (!wrap) return;
  var row = document.createElement("div");
  row.className = "kind-rij";
  // Neem het veld over van de rij die er al staat: het probeer-formulier vraagt
  // de leeftijd, het accountformulier het geboortejaar. Anders vroeg het eerste
  // kind om een leeftijd en het tweede om een geboortejaar.
  var voorbeeld = wrap.querySelector("input");
  var jaar = new Date().getFullYear();
  if (voorbeeld) {
    var kopie = voorbeeld.cloneNode(true);
    kopie.value = "";
    kopie.removeAttribute("id");
    row.appendChild(kopie);
  } else {
    var inp = document.createElement("input");
    inp.type = "number"; inp.name = "birth_year";
    inp.min = jaar - 17; inp.max = jaar;
    inp.placeholder = "geboortejaar (bv. " + (jaar - 6) + ")";
    inp.setAttribute("inputmode", "numeric");
    row.appendChild(inp);
  }
  var weg = document.createElement("button");
  weg.type = "button"; weg.className = "kind-weg";
  weg.setAttribute("aria-label", "verwijder"); weg.textContent = "×";
  row.appendChild(weg);
  wrap.appendChild(row);
  row.querySelector("input").focus();
});
document.addEventListener("click", function (e) {
  var x = e.target.closest(".kind-weg");
  if (!x) return;
  e.preventDefault();
  var rows = document.querySelectorAll("#kinderen .kind-rij");
  if (rows.length > 1) x.closest(".kind-rij").remove();
});

// Live filteren + zoeken van de lijst (Vandaag/Weekend) — puur client-side
(function () {
  var actiefFilter = "alles";
  var zoekterm = "";

  function pasToe() {
    var kaarten = document.querySelectorAll(".kaart-event");
    var zichtbaar = 0;
    kaarten.forEach(function (card) {
      var toonF = true;
      if (actiefFilter === "gratis") toonF = card.dataset.free === "1";
      else if (actiefFilter === "binnen") toonF = card.dataset.indoor === "1";
      else if (actiefFilter === "buiten") toonF = card.dataset.indoor === "0";
      else if (actiefFilter === "score") toonF = parseFloat(card.dataset.score || "0") >= 4;
      var toonZ = !zoekterm || (card.dataset.zoek || "").indexOf(zoekterm) !== -1;
      var toon = toonF && toonZ;
      card.style.display = toon ? "" : "none";
      if (toon) zichtbaar++;
    });
    var teller = document.getElementById("teller");
    if (teller) {
      var totaal = kaarten.length;
      if (zichtbaar === totaal) teller.textContent = totaal + " activiteiten";
      else if (zichtbaar === 0) teller.textContent = "Geen resultaten — probeer een andere filter of zoekterm";
      else teller.textContent = zichtbaar + " van " + totaal + " activiteiten";
    }
    // lege-staat kaart tonen/verbergen
    var leeg = document.getElementById("geen-resultaat");
    if (leeg) leeg.style.display = zichtbaar === 0 ? "" : "none";
  }

  document.addEventListener("click", function (e) {
    var chip = e.target.closest(".filterchip");
    if (!chip) return;
    chip.closest(".filterbalk").querySelectorAll(".filterchip").forEach(function (c) {
      c.classList.remove("aan");
    });
    chip.classList.add("aan");
    actiefFilter = chip.dataset.filter;
    pasToe();
  });

  document.addEventListener("input", function (e) {
    if (e.target.id !== "zoek") return;
    zoekterm = e.target.value.trim().toLowerCase();
    pasToe();
  });

  if (document.getElementById("filterbalk")) pasToe();
})();

// Deelknop op eventpagina (native share, met kopieer-fallback)
document.addEventListener("click", function (e) {
  var btn = e.target.closest("#deel, .deel-knop");
  if (!btn) return;
  var data = { title: btn.dataset.titel, text: "Leuk voor het gezin: " + btn.dataset.titel, url: btn.dataset.url };
  if (navigator.share) {
    navigator.share(data).catch(function () {});
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(btn.dataset.url).then(function () {
      btn.textContent = "✓ Link gekopieerd";
      setTimeout(function () { btn.textContent = "📤 Deel"; }, 2000);
    });
  } else {
    window.open("https://wa.me/?text=" + encodeURIComponent(data.text + " " + data.url), "_blank");
  }
});

// Hamburger-menu (mobiel)
(function(){
  var h = document.getElementById("hamburger");
  var m = document.getElementById("mobiel-menu");
  if (h && m) {
    h.addEventListener("click", function(){
      var open = m.classList.toggle("open");
      h.classList.toggle("open", open);
      h.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
})();

// Cookiebanner — echte werking, keuze bewaard in een cookie (1 jaar)
(function () {
  function getCookie(naam) {
    var m = document.cookie.match("(^|;)\\s*" + naam + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : null;
  }
  function setCookie(naam, waarde, dagen) {
    var d = new Date();
    d.setTime(d.getTime() + dagen * 864e5);
    document.cookie = naam + "=" + waarde + ";expires=" + d.toUTCString() +
                      ";path=/;SameSite=Lax";
  }
  var banner = document.getElementById("cookiebanner");
  if (!banner) return;
  // Al een keuze gemaakt? Dan banner niet tonen.
  if (getCookie("cookie_keuze")) return;
  banner.hidden = false;

  function bewaar(analytisch) {
    setCookie("cookie_keuze", analytisch ? "alles" : "functioneel", 365);
    banner.hidden = true;
    // Analytische scripts zou je hier kunnen activeren als cookie_keuze=alles.
  }
  var a = document.getElementById("cb-analytisch");
  document.getElementById("cb-alles").addEventListener("click", function () { bewaar(true); });
  document.getElementById("cb-weiger").addEventListener("click", function () { bewaar(false); });
  document.getElementById("cb-bewaar").addEventListener("click", function () {
    bewaar(a && a.checked);
  });
})();

// Kapotte thumbnail-foto's opruimen zodat de emoji-onderlaag zichtbaar wordt.
// Vangt ook afbeeldingen die al mislukt waren vóór deze code liep (cache/lazy):
// een geladen maar 0px-brede img is stuk. De inline onerror dekt live fouten.
(function () {
  function scrub() {
    document.querySelectorAll("img.thumb-over").forEach(function (img) {
      function weg() { if (img && img.parentNode) img.remove(); }
      if (img.complete && img.naturalWidth === 0) weg();
      img.addEventListener("error", weg);
    });
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", scrub);
  else scrub();
})();

/* Foto's verkleinen ín de browser vóór het uploaden: sneller op mobiel,
   minder dataverbruik en minder serverbelasting. De server verkleint sowieso
   nog eens (veiligheidsnet), dus dit is puur progressive enhancement. */
document.addEventListener('change', function (e) {
  var input = e.target;
  if (!(input instanceof HTMLInputElement) || input.type !== 'file'
      || input.name !== 'foto' || !input.files || !input.files[0]) return;
  var file = input.files[0];
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) return;
  if (file.size < 600 * 1024) return;              // al klein genoeg
  var MAX = 1600, url = URL.createObjectURL(file), img = new Image();
  img.onload = function () {
    URL.revokeObjectURL(url);
    var schaal = Math.min(1, MAX / Math.max(img.width, img.height));
    if (schaal >= 1) return;                       // klein beeld, groot bestand: laat de server het doen
    var c = document.createElement('canvas');
    c.width = Math.round(img.width * schaal);
    c.height = Math.round(img.height * schaal);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    c.toBlob(function (blob) {
      if (!blob || blob.size >= file.size) return; // enkel vervangen als het écht kleiner is
      var dt = new DataTransfer();
      dt.items.add(new File([blob], 'foto.jpg', {type: 'image/jpeg'}));
      input.files = dt.files;
    }, 'image/jpeg', 0.85);
  };
  img.onerror = function () { URL.revokeObjectURL(url); };
  img.src = url;
});


/* Fiche-actie "Foto toevoegen": open het uploadblok en scroll ernaartoe. */
document.addEventListener('click', function (e) {
  var knop = e.target.closest('[data-open-foto]');
  if (!knop) return;
  var blok = document.getElementById('foto-blok');
  if (blok) { blok.open = true; blok.scrollIntoView({behavior: 'smooth'}); e.preventDefault(); }
});

// Terugknop: ga écht terug naar waar je vandaan kwam (kaart mét zoomstand,
// gefilterde lijst, ...). Extern bestand: de CSP blokkeert inline onclick —
// daardoor viel de knop stilletjes terug op zijn vaste href.
(function () {
  document.addEventListener("click", function (e) {
    var knop = e.target.closest("[data-terug]");
    if (!knop) return;
    var eigenSite = document.referrer &&
        document.referrer.indexOf(window.location.origin) === 0;
    if (eigenSite && history.length > 1) {
      e.preventDefault();
      history.back();
    }
    // anders (rechtstreeks binnengekomen via deellink/Google): volg de href
  });
})();

// -- Generieke gedragspatronen (CSP-veilig, geen inline handlers) ------------
// data-confirm op een <form>: vraagt bevestiging vóór verzenden.
// data-confirm op een <button>/<a>: vraagt bevestiging vóór de klik doorgaat.
// data-autosubmit op een <select>: verstuurt het formulier bij wijziging.
// data-uitvoer op een <input type=range>: schrijft de waarde naar #<id>.
(function () {
  "use strict";
  document.addEventListener("submit", function (e) {
    var f = e.target.closest ? e.target : null;
    if (f && f.dataset && f.dataset.confirm && !window.confirm(f.dataset.confirm)) {
      e.preventDefault();
    }
  });
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (!el || el.tagName === "FORM") return;
    if (!window.confirm(el.dataset.confirm)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener("change", function (e) {
    var el = e.target;
    if (el.matches && el.matches("select[data-autosubmit]") && el.form) {
      if (el.form.requestSubmit) el.form.requestSubmit();
      else el.form.submit();
    }
  });
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (el.matches && el.matches("[data-uitvoer]")) {
      var doel = document.getElementById(el.dataset.uitvoer);
      if (doel) doel.textContent = el.value;
    }
  });
})();

// data-kopieer op een knop: kopieert de inhoud van het element met dat id.
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-kopieer]");
    if (!b) return;
    var el = document.getElementById(b.dataset.kopieer);
    if (!el) return;
    navigator.clipboard.writeText(el.value || el.textContent).then(function () {
      var oud = b.textContent;
      b.textContent = "Gekopieerd ✓";
      setTimeout(function () { b.textContent = oud; }, 1500);
    });
  });
})();

// Lichtgewicht lightbox voor fichefoto's (data-lightbox): klik = groot bekijken,
// pijltjes/knoppen = bladeren, klik op het beeld = zoom (menukaart leesbaar).
(function () {
  "use strict";
  var lijst = [], index = 0, overlay = null, beeld = null, gezoomd = false;

  function bouw() {
    overlay = document.createElement("div");
    overlay.className = "lightbox";
    overlay.innerHTML =
      '<button class="lb-sluit" aria-label="Sluiten">✕</button>' +
      '<button class="lb-vorige" aria-label="Vorige">‹</button>' +
      '<div class="lb-houder"><img alt=""></div>' +
      '<button class="lb-volgende" aria-label="Volgende">›</button>' +
      '<p class="lb-hint">Tik op de foto om in te zoomen</p>';
    document.body.appendChild(overlay);
    beeld = overlay.querySelector("img");
    overlay.querySelector(".lb-sluit").addEventListener("click", sluit);
    overlay.querySelector(".lb-vorige").addEventListener("click", function () { toon(index - 1); });
    overlay.querySelector(".lb-volgende").addEventListener("click", function () { toon(index + 1); });
    overlay.addEventListener("click", function (e) { if (e.target === overlay) sluit(); });
    beeld.addEventListener("click", function () {
      gezoomd = !gezoomd;
      overlay.classList.toggle("lb-zoom", gezoomd);
    });
    document.addEventListener("keydown", function (e) {
      if (!overlay || overlay.hidden) return;
      if (e.key === "Escape") sluit();
      if (e.key === "ArrowLeft") toon(index - 1);
      if (e.key === "ArrowRight") toon(index + 1);
    });
  }
  function toon(i) {
    index = (i + lijst.length) % lijst.length;
    gezoomd = false;
    overlay.classList.remove("lb-zoom");
    beeld.src = lijst[index];
    overlay.querySelector(".lb-vorige").hidden = lijst.length < 2;
    overlay.querySelector(".lb-volgende").hidden = lijst.length < 2;
    overlay.hidden = false;
    document.body.style.overflow = "hidden";
  }
  function sluit() {
    overlay.hidden = true;
    document.body.style.overflow = "";
  }
  document.addEventListener("click", function (e) {
    var img = e.target.closest("img[data-lightbox]");
    if (!img) return;
    e.preventDefault();
    lijst = Array.prototype.map.call(
      document.querySelectorAll("img[data-lightbox]"),
      function (el) { return el.src; });
    if (!overlay) bouw();
    toon(lijst.indexOf(img.src));
  });
})();

// Uitsnede-schuifje op de uitbater-fiche: live voorbeeld bij het schuiven.
(function () {
  "use strict";
  document.addEventListener("input", function (e) {
    var s = e.target.closest("input[data-focus-doel]");
    if (!s) return;
    var img = document.getElementById(s.dataset.focusDoel);
    if (img) img.style.objectPosition = "50% " + s.value + "%";
  });
})();
