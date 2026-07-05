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
