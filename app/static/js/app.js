// PWA: service worker + installatieprompt ("Zet Ravot op je beginscherm")
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(() => {});
}
let deferredPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
  const el = document.getElementById("install-cta");
  if (el) {
    el.hidden = false;
    el.addEventListener("click", async () => {
      el.hidden = true;
      if (deferredPrompt) { deferredPrompt.prompt(); deferredPrompt = null; }
    });
  }
});

// Dynamisch kinderen toevoegen (onbeperkt) in onboarding/profiel
document.addEventListener("click", function (e) {
  var btn = e.target.closest("[data-add-kind]");
  if (!btn) return;
  e.preventDefault();
  var wrap = document.getElementById("kinderen");
  if (!wrap) return;
  var row = document.createElement("div");
  row.className = "kind-rij";
  var jaar = new Date().getFullYear();
  row.innerHTML = '<input type="number" name="birth_year" min="' + (jaar - 17) + '" max="' + jaar +
    '" placeholder="geboortejaar (bv. ' + (jaar - 6) + ')" inputmode="numeric">' +
                  '<button type="button" class="kind-weg" aria-label="verwijder">×</button>';
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
