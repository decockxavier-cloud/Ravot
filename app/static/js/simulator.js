// Prijssimulator in het beheer (patch 158). Zelfde rekenmodel als het
// ontwerp-artifact: drie formules, kortingsvorm (€ of looptijd), commissie-%,
// exclusiviteitscaps (feest 0 = onbeperkt) en het verlengingsjaar.
(function () {
  "use strict";
  var wortel = document.getElementById("sim");
  if (!wortel) return;

  var st = {
    prijsP: parseFloat(wortel.dataset.prijsP) || 200,
    prijsF: parseFloat(wortel.dataset.prijsF) || 250,
    korting: 20,
    kortingsvorm: "prijs",
    gemeenten: 20,
    capZ: parseInt(wortel.dataset.capZ, 10) || 4,
    capF: parseInt(wortel.dataset.capF, 10) || 0,
    bezZ: 30,
    feestPerGem: 2,
    combiDeel: 30,
    commissie: 15,
    retentie: 80,
  };
  // Startkorting afleiden uit de echte combiprijs t.o.v. de som.
  var somStart = st.prijsP + st.prijsF;
  var combiStart = parseFloat(wortel.dataset.prijsC) || 360;
  if (somStart > 0) st.korting = Math.round((1 - combiStart / somStart) * 100);

  function eur(n) {
    return "€" + Math.round(n).toLocaleString("nl-BE");
  }
  function zet(naam, html) {
    var el = wortel.querySelector('[data-uit="' + naam + '"]');
    if (el) el.innerHTML = html;
  }
  function stippen(vol, totaal, kleurVol) {
    var uit = "";
    for (var i = 0; i < totaal; i++) {
      var gevuld = i < vol;
      uit += '<span class="sim-stip" style="' +
        (gevuld ? "background:" + kleurVol + ";border-color:" + kleurVol
                : "border-color:#ddd") + '"></span>';
    }
    return uit;
  }

  function reken() {
    var inGeld = st.kortingsvorm === "prijs";
    var som = st.prijsP + st.prijsF;
    var combiPrijs = inGeld ? Math.round(som * (1 - st.korting / 100)) : som;
    var extraMaanden = inGeld ? 0 : Math.max(1, Math.round(12 * st.korting / 100));

    var z = Math.round(st.gemeenten * st.capZ * st.bezZ / 100);
    var f = Math.round(st.gemeenten * st.feestPerGem);
    if (st.capF > 0) f = Math.min(f, st.gemeenten * st.capF);
    var combi = Math.round(Math.min(z, f) * st.combiDeel / 100);
    var pureP = z - combi, pureF = f - combi;
    var deals = pureP + pureF + combi;

    var omzet = pureP * st.prijsP + pureF * st.prijsF + combi * combiPrijs;
    var verkoper = Math.round(omzet * st.commissie / 100);
    var netto = omzet - verkoper;
    var omzet2 = Math.round(omzet * st.retentie / 100);

    // knoppenwaarden
    zet("prijsP", eur(st.prijsP));
    zet("prijsF", eur(st.prijsF));
    zet("korting", st.korting + "%");
    zet("gemeenten", st.gemeenten);
    zet("capZ", st.capZ);
    zet("capF", st.capF === 0 ? "∞" : st.capF);
    zet("bezZ", st.bezZ + "%");
    zet("feestPerGem", st.feestPerGem);
    zet("combiDeel", st.combiDeel + "%");
    zet("commissie", st.commissie + "%");
    zet("retentie", st.retentie + "%");
    zet("combiUitleg", inGeld
      ? "In geld: combi kost " + eur(combiPrijs) + " i.p.v. " + eur(som)
      : "In looptijd: combi kost " + eur(combiPrijs) + " voor " + (12 + extraMaanden) +
        " maanden (" + extraMaanden + " gratis)");
    zet("commissiePerDeal", "Per deal: ⭐ " + eur(st.prijsP * st.commissie / 100) +
      " · 🎉 " + eur(st.prijsF * st.commissie / 100) +
      " · 🤝 " + eur(combiPrijs * st.commissie / 100) + " — de combi loont het meest.");

    // resultaten
    zet("nP", pureP); zet("omzetP", "× " + eur(st.prijsP) + " = " + eur(pureP * st.prijsP));
    zet("nF", pureF); zet("omzetF", "× " + eur(st.prijsF) + " = " + eur(pureF * st.prijsF));
    zet("nC", combi); zet("omzetC", "× " + eur(combiPrijs) + " = " + eur(combi * combiPrijs));
    zet("deals", deals);
    zet("omzet", eur(omzet));
    zet("verkoper", "− " + eur(verkoper) + " (" + st.commissie + "%)");
    zet("netto", eur(netto));
    zet("perGemeente", st.gemeenten ? eur(omzet / st.gemeenten) : "—");
    zet("retentieTekst", st.retentie + "% verlengt, zonder verkoopfee");
    zet("omzet2", eur(omzet2));
    zet("looptijdNoot", !inGeld && combi > 0
      ? "Let op: de " + combi + " combi-verlengingen komen " + extraMaanden +
        " maand(en) later binnen — zelfde geld, iets later op de rekening."
      : "");

    // één gemeente onder de loep
    var gZ = Math.min(st.capZ, Math.round(st.capZ * st.bezZ / 100));
    var loepFtot = st.capF > 0 ? st.capF : Math.max(st.feestPerGem, 1);
    var gF = Math.min(loepFtot, st.feestPerGem);
    var gC = Math.min(gZ, gF, Math.round(Math.min(gZ, gF) * st.combiDeel / 100));
    zet("loepZTekst", "Zichtbaarheid: " + gZ + "/" + st.capZ + " plekken bezet" +
      (gC ? " (waarvan " + gC + " combi)" : ""));
    zet("loepZ", stippen(gZ, st.capZ, "#EE8035"));
    zet("loepFTekst", "Feest: " + gF + (st.capF > 0 ? "/" + st.capF : " (onbeperkt)") +
      " bezet" + (gC ? " (waarvan " + gC + " combi)" : ""));
    zet("loepF", stippen(gF, loepFtot, "#4CA362"));
  }

  wortel.addEventListener("input", function (e) {
    var s = e.target.closest("input[data-in]");
    if (!s) return;
    st[s.dataset.in] = Number(s.value);
    reken();
  });
  wortel.addEventListener("click", function (e) {
    var b = e.target.closest(".sim-vorm");
    if (!b) return;
    st.kortingsvorm = b.dataset.vorm;
    wortel.querySelectorAll(".sim-vorm").forEach(function (x) {
      x.classList.toggle("on", x === b);
    });
    reken();
  });

  // schuiven op de startwaarden zetten
  wortel.querySelectorAll("input[data-in]").forEach(function (s) {
    if (st[s.dataset.in] !== undefined) s.value = st[s.dataset.in];
  });
  reken();
})();
