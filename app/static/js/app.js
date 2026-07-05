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
  row.innerHTML = '<input type="number" name="age" min="0" max="17" placeholder="leeftijd" inputmode="numeric">' +
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

// Live filteren van de lijst (Vandaag/Weekend) — puur client-side
document.addEventListener("click", function (e) {
  var chip = e.target.closest(".filterchip");
  if (!chip) return;
  var balk = chip.closest(".filterbalk");
  balk.querySelectorAll(".filterchip").forEach(function (c) { c.classList.remove("aan"); });
  chip.classList.add("aan");
  var f = chip.dataset.filter;
  document.querySelectorAll(".kaart-event").forEach(function (card) {
    var toon = true;
    if (f === "gratis") toon = card.dataset.free === "1";
    else if (f === "binnen") toon = card.dataset.indoor === "1";
    else if (f === "buiten") toon = card.dataset.indoor === "0";
    else if (f === "score") toon = parseFloat(card.dataset.score || "0") >= 4;
    card.style.display = toon ? "" : "none";
  });
});
