"""Zelf-curerende velden — fase 0 t/m 2.

Eén teller per veld per plek. De bron (OSM/Overture) is de startstem; gebruikers
voegen hun gewicht toe. De getoonde waarde is welke kant zwaarder weegt. Er is
nooit meer dan één waarde per veld — geen twee cijfers die tegenspreken.

Fase 2 voegt toe:
- Veroudering: oude stemmen wegen geleidelijk lichter, zodat de fiche meebeweegt
  met de werkelijkheid en een gekantelde situatie zichzelf corrigeert.
- Drie toestanden: onbekend → voorlopig (getoond, nog te versterken) →
  bevestigd (staat vast). Zo vergrendelt één losse stem een veld niet meer.
- Eerlijke twijfel: bij tegenspraak leveren we de verhouding (bv. 8 van 10),
  zodat de weergave "meestal ja" kan tonen i.p.v. stellig te doen.

De weergavelogica (KISS) houdt dit bewust op één plek; de rest van de code vraagt
enkel veld_status() en hoeft niets te weten van gewichten of halfwaardetijden.
"""
import math
from datetime import datetime

from .extensions import db
from .models import (VeldStem, ZACHTE_VELDEN, ANONIEM_GEWICHT,
                     BRON_GEWICHT, GEBRUIKER_BASIS_GEWICHT)


# Halfwaardetijd per soort veld, in dagen: na deze periode weegt een stem nog
# half. Vluchtige info veroudert sneller dan stabiele. Bewust aan de voorzichtige
# (trage) kant — bij te lang blijven hangen kan dit later omlaag.
HALFWAARDE_DAGEN = {
    # vluchtig: kan snel veranderen
    "terras": 400, "overdekt_terras": 400, "kindermenu": 400,
    # stabiel: verandert zelden
    "toilet": 900, "drinkwater": 900, "picknick": 900, "parking": 900,
    "speelhoek": 700, "kinderstoel": 700, "omheind": 1100,
    "toegankelijk": 1100, "verzorgingstafel": 900, "buggy_ok": 900,
    "allergievriendelijk": 500, "babyvoeding": 700, "huisdieren": 900,
}
STANDAARD_HALFWAARDE = 800

# --- Welke velden zijn relevant voor welk soort plek? -----------------------
# Niet alles is overal van toepassing: "kindermenu?" bij een speeltuin is onzin,
# "speelhoek?" bij een museum ook. We tonen per plek enkel de zinnige vragen.
# 'ALGEMEEN' geldt overal; daarnaast per familie de specifieke velden.
VELDEN_ALGEMEEN = ["toilet", "parking", "toegankelijk", "huisdieren"]

# Buitenplekken: speeltuin, park, natuur, speelbos...
VELDEN_BUITEN = ["drinkwater", "picknick", "omheind", "buggy_ok"]
# Eetplekken: horeca, bars, kinderboerderij-met-cafetaria...
VELDEN_HORECA = ["kindermenu", "kinderstoel", "terras", "overdekt_terras",
                 "speelhoek", "allergievriendelijk", "babyvoeding"]
# Bezoekplekken (binnen of gemengd): museum, kasteel, zoo, pretpark...
VELDEN_BEZOEK = ["kinderstoel", "verzorgingstafel", "buggy_ok", "speelhoek"]

# Per type-code de familie. Onbekende types → enkel de algemene velden.
_TYPE_VELDEN = {
    # buiten
    "playground": VELDEN_BUITEN + ["verzorgingstafel"],
    "park": VELDEN_BUITEN, "nature_reserve": VELDEN_BUITEN,
    "swimming_area": VELDEN_BUITEN, "viewpoint": ["drinkwater", "picknick"],
    "uit_wandeling": ["drinkwater", "picknick"],
    # bezoek
    "museum": VELDEN_BEZOEK, "castle": VELDEN_BEZOEK,
    "zoo": VELDEN_BEZOEK + ["picknick", "drinkwater"],
    "aquarium": VELDEN_BEZOEK,
    "theme_park": VELDEN_BEZOEK + ["picknick"],
    "water_park": ["kinderstoel", "verzorgingstafel", "terras"],
    "attraction": VELDEN_BEZOEK, "miniature_golf": ["terras", "drinkwater"],
    "uit_kinderboerderij": VELDEN_BEZOEK + ["picknick", "drinkwater"],
    "uit_indoorspeeltuin": VELDEN_HORECA + ["verzorgingstafel"],
    # eten
    "horeca": VELDEN_HORECA, "zomerbar": VELDEN_HORECA,
    "winterbar": VELDEN_HORECA,
}


def relevante_velden(event):
    """De zachte velden die zinnig zijn om te vragen voor déze plek, in een
    stabiele volgorde. Algemene velden eerst, dan de type-specifieke."""
    code = getattr(event, "subtype", None)
    specifiek = _TYPE_VELDEN.get(code, [])
    volgorde = []
    for v in VELDEN_ALGEMEEN + specifiek:
        if v in ZACHTE_VELDEN and v not in volgorde:
            volgorde.append(v)
    return volgorde

# Drempels voor de drie toestanden (netto gewicht = |ja - nee|).
# Eén verse gebruikersstem (gewicht ~1) tilt een veld al naar 'voorlopig':
# het wordt getoond, maar blijft vragen om bevestiging tot het stevig staat.
DREMPEL_BEVESTIGD = 2.5   # genoeg overeenstemming → staat vast, geen vraag meer
# twijfel: als de minderheid een noemenswaardig deel is, tonen we "meestal ja"
TWIJFEL_AANDEEL = 0.20    # ≥20% tegenstem → eerlijk als "meestal" tonen

# --- Fase 3: vertrouwen -----------------------------------------------------
# Het gewicht van een stemmer wordt LIVE uit zijn geschiedenis berekend (zoals
# de badges), niet als kolom bewaard: geen migratie, altijd consistent, nooit
# verouderd. Onzichtbaar voor de gebruiker.
VERTROUWEN_MIN = 0.4      # ondergrens: een onruststoker telt nooit helemaal weg
VERTROUWEN_MAX = 2.5      # bovengrens: één bewezen bijdrager domineert niet
# Tijdvenster waarbinnen stemmen "tijdgenoten" zijn (dagen). Klopte je met wie
# in dezelfde periode stemde? Niet met de verre toekomst.
VENSTER_DAGEN = 120
# Gevoelige velden: hogere drempel vóór ze doorwerken, want een fout heeft grote
# of moeilijk terug te draaien gevolgen. (Deze staan niet in ZACHTE_VELDEN.)
GEVOELIGE_VELDEN = {"gesloten", "niet_kindvriendelijk"}
DREMPEL_GEVOELIG = 5.5


def stemmer_vertrouwen(stemmer, nu=None):
    """Live gewicht van één stemmer (family-id als tekst), tussen VERTROUWEN_MIN
    en VERTROUWEN_MAX. De bron heeft een vast, neutraal gewicht.

    Principe (jouw keuze): je klopt of niet t.o.v. je TIJDGENOTEN, niet t.o.v.
    de verre toekomst. Voor elke stem die je ooit gaf, kijken we of ze
    overeenkwam met wat andere stemmers in hetzelfde tijdvenster op datzelfde
    veld zeiden. Veel overeenkomst → gewicht omhoog; stelselmatig ertegen in →
    omlaag. Wie de wereld ziet veranderen en dat meldt, wordt NIET gestraft,
    want de tijdgenoten zien dezelfde verandering.
    """
    if stemmer == "bron":
        return BRON_GEWICHT
    if stemmer.startswith("anon:"):
        # Geen account = geen betrouwbare geschiedenis; vast, lager gewicht.
        return ANONIEM_GEWICHT
    nu = nu or datetime.utcnow()
    from datetime import timedelta
    eigen = VeldStem.query.filter_by(stemmer=stemmer).all()
    if not eigen:
        return GEBRUIKER_BASIS_GEWICHT
    juist = 0.0
    fout = 0.0
    for s in eigen:
        moment = s.updated_at or s.created_at or nu
        venster_start = moment - timedelta(days=VENSTER_DAGEN)
        venster_eind = moment + timedelta(days=VENSTER_DAGEN)
        # tijdgenoten: andere stemmers, zelfde veld+plek, binnen het venster
        buren = VeldStem.query.filter(
            VeldStem.event_id == s.event_id, VeldStem.veld == s.veld,
            VeldStem.stemmer != stemmer, VeldStem.stemmer != "bron").all()
        ja = nee = 0
        for b in buren:
            bm = b.updated_at or b.created_at or nu
            if venster_start <= bm <= venster_eind:
                if b.waarde:
                    ja += 1
                else:
                    nee += 1
        if ja == nee:
            continue                 # geen tijdgenoten of gelijkspel: neutraal
        tijdgenoten_zeggen = ja > nee
        if s.waarde == tijdgenoten_zeggen:
            juist += 1
        else:
            fout += 1
    if juist + fout == 0:
        return GEBRUIKER_BASIS_GEWICHT
    # verhouding juist/totaal, geschaald rond het basisgewicht
    ratio = juist / (juist + fout)
    gewicht = GEBRUIKER_BASIS_GEWICHT * (0.5 + ratio)   # 0.5x .. 1.5x
    return max(VERTROUWEN_MIN, min(VERTROUWEN_MAX, gewicht))


def _stemmer_id(family, anon_id=None):
    if family is not None:
        return str(family.id)
    if anon_id:
        return f"anon:{anon_id}"[:60]
    return "bron"


def leg_stem_vast(event_id, veld, waarde, family=None, gewicht=None,
                  anon_id=None):
    """Registreer of wijzig één stem. Eén stem per (plek, veld, stemmer): wie
    van gedacht verandert, past zijn stem aan i.p.v. te stapelen.

    Retourneert de VeldStem-rij. Commit gebeurt door de aanroeper.
    """
    stemmer = _stemmer_id(family, anon_id)
    if gewicht is None:
        if family is not None:
            gewicht = GEBRUIKER_BASIS_GEWICHT
        elif anon_id:
            gewicht = ANONIEM_GEWICHT
        else:
            gewicht = BRON_GEWICHT
    rij = VeldStem.query.filter_by(event_id=event_id, veld=veld,
                                   stemmer=stemmer).first()
    if rij is None:
        rij = VeldStem(event_id=event_id, veld=veld, stemmer=stemmer,
                       waarde=bool(waarde), gewicht=gewicht)
        db.session.add(rij)
    else:
        rij.waarde = bool(waarde)
        rij.gewicht = gewicht
        rij.updated_at = datetime.utcnow()
    return rij


# Stem van een toeristische dienst via de gemeentelink (patch 265). Zwaarder
# dan een gewone stem — de dienst kent zijn eigen terreinen — maar bewust onder
# VERTROUWEN_MAX: één administratieve vergissing mag door gezinnen ter plaatse
# bijgestuurd kunnen worden.
GEMEENTE_GEWICHT = 2.0


def leg_gemeente_stem_vast(event_id, veld, waarde, gemeente):
    """Registreer of wijzig de stem van de gemeentedienst zelf (patch 265).

    Eén stem per (gemeente, plek, veld), net als elke andere stemmer; de
    dedupe-sleutel is 'gemeente:<naam>'. Commit gebeurt door de aanroeper.
    """
    stemmer = f"gemeente:{gemeente}"[:40]
    rij = VeldStem.query.filter_by(event_id=event_id, veld=veld,
                                   stemmer=stemmer).first()
    if rij is None:
        rij = VeldStem(event_id=event_id, veld=veld, stemmer=stemmer,
                       waarde=bool(waarde), gewicht=GEMEENTE_GEWICHT)
        db.session.add(rij)
    else:
        rij.waarde = bool(waarde)
        rij.gewicht = GEMEENTE_GEWICHT
        rij.updated_at = datetime.utcnow()
    return rij


def veldstatussen_batch(event_ids, nu=None):
    """{event_id: {veld: status-dict}} voor een lijst plekken in ÉÉN query
    (patch 265). Voor lijstpagina's zoals de gemeentelink: alle_velden() per
    plek aanroepen zou één query per plek betekenen. Zelfde gedeelde 'nu' en
    vertrouwen-cache als alle_velden(), dus identieke uitkomsten.
    """
    nu = nu or datetime.utcnow()
    ids = [i for i in event_ids if i]
    if not ids:
        return {}
    per_plek = {}
    for r in VeldStem.query.filter(VeldStem.event_id.in_(ids)).all():
        per_plek.setdefault(r.event_id, {}).setdefault(r.veld, []).append(r)
    cache = {}
    return {eid: {veld: veld_status(eid, veld, rijen, nu=nu,
                                    vertrouwen_cache=cache)
                  for veld, rijen in velden.items()}
            for eid, velden in per_plek.items()}


def _verval_factor(veld, stem, nu=None):
    """Hoeveel weegt deze stem nog, na veroudering? 1.0 vers, → 0 met de tijd.
    Exponentieel verval met een halfwaardetijd die van het veld afhangt."""
    nu = nu or datetime.utcnow()
    moment = stem.updated_at or stem.created_at or nu
    dagen = max(0.0, (nu - moment).total_seconds() / 86400.0)
    halfwaarde = HALFWAARDE_DAGEN.get(veld, STANDAARD_HALFWAARDE)
    return 0.5 ** (dagen / halfwaarde)


def _weeg(stemmen, veld, nu=None, vertrouwen_cache=None):
    """Splits in (gewicht_ja, gewicht_nee), mét veroudering én vertrouwen.

    De enige plek waar veroudering (fase 2) en vertrouwen (fase 3) ingrijpen —
    de rest van de code merkt er niets van. Elke stem telt voor:
        basisgewicht × verval-door-tijd × vertrouwen-van-de-stemmer
    """
    nu = nu or datetime.utcnow()
    cache = vertrouwen_cache if vertrouwen_cache is not None else {}
    ja = nee = 0.0
    for s in stemmen:
        if s.stemmer not in cache:
            cache[s.stemmer] = stemmer_vertrouwen(s.stemmer, nu)
        vertrouwen = cache[s.stemmer]
        # de bronstem heeft zijn gewicht al in s.gewicht; niet dubbel wegen
        basis = s.gewicht if s.stemmer == "bron" else vertrouwen
        g = basis * _verval_factor(veld, s, nu)
        if s.waarde:
            ja += g
        else:
            nee += g
    return ja, nee


def veld_status(event_id, veld, stemmen=None, nu=None, vertrouwen_cache=None):
    """De uitkomst voor één veld, als dict:

        {waarde, ja, nee, herkomst, toestand, meerderheid_pct}

    - waarde     True|False|None  (None = onbekend)
    - toestand   'onbekend' | 'voorlopig' | 'bevestigd'
    - herkomst   'bezoekers' | 'bron' | 'geen'
    - meerderheid_pct  0-100: aandeel van de winnende kant (voor "meestal ja").
                       None als er geen twijfel is (eenparig of onbekend).
    """
    if stemmen is None:
        stemmen = VeldStem.query.filter_by(event_id=event_id, veld=veld).all()
    if not stemmen:
        return {"waarde": None, "ja": 0.0, "nee": 0.0, "herkomst": "geen",
                "toestand": "onbekend", "meerderheid_pct": None}

    nu = nu or datetime.utcnow()
    ja, nee = _weeg(stemmen, veld, nu, vertrouwen_cache)
    heeft_gebruiker = any(s.stemmer != "bron" for s in stemmen)
    herkomst = "bezoekers" if heeft_gebruiker else "bron"

    if ja == nee:
        bron = next((s for s in stemmen if s.stemmer == "bron"), None)
        waarde = bron.waarde if bron is not None else None
    else:
        waarde = ja > nee

    netto = abs(ja - nee)
    totaal = ja + nee
    drempel = DREMPEL_GEVOELIG if veld in GEVOELIGE_VELDEN else DREMPEL_BEVESTIGD
    if waarde is None or totaal <= 0:
        toestand = "onbekend"
    elif netto >= drempel:
        toestand = "bevestigd"
    else:
        toestand = "voorlopig"

    # Twijfelpercentage: enkel tonen als de minderheid noemenswaardig is.
    meerderheid_pct = None
    if totaal > 0 and waarde is not None:
        winnend = max(ja, nee)
        aandeel_minderheid = (totaal - winnend) / totaal
        if aandeel_minderheid >= TWIJFEL_AANDEEL:
            meerderheid_pct = round(100 * winnend / totaal)

    return {"waarde": waarde, "ja": ja, "nee": nee, "herkomst": herkomst,
            "toestand": toestand, "meerderheid_pct": meerderheid_pct}


def alle_velden(event_id, nu=None):
    """Alle velden met stemmen voor één plek → {veld: status-dict}. Eén query,
    één gedeeld 'nu'-moment én één gedeelde vertrouwen-cache (fase 3), zodat de
    weging consistent en zonder herberekening gebeurt."""
    nu = nu or datetime.utcnow()
    rijen = VeldStem.query.filter_by(event_id=event_id).all()
    per_veld = {}
    for r in rijen:
        per_veld.setdefault(r.veld, []).append(r)
    cache = {}
    return {veld: veld_status(event_id, veld, stemmen, nu=nu,
                              vertrouwen_cache=cache)
            for veld, stemmen in per_veld.items()}


# --- Fase 3: plausibiliteitscheks -------------------------------------------
# Simpele als-dan-regels die onmogelijke combinaties markeren voor de admin-
# wachtrij. Geen intelligentie, geen ML — enkel gezond verstand in code.
def plausibiliteitswaarschuwingen(event):
    """Retourneert een lijst korte waarschuwingen over onmogelijke of verdachte
    combinaties op één plek. Leeg = niets aan de hand."""
    uit = []
    amin = getattr(event, "age_min", None)
    amax = getattr(event, "age_max", None)
    if amin is not None and amax is not None:
        if amin > amax:
            uit.append(f"leeftijd van-tot omgekeerd ({amin}-{amax})")
        if amax - amin <= 1 and getattr(event, "subtype", None) == "playground":
            uit.append(f"speeltuin met heel smalle leeftijd ({amin}-{amax})")
    # gratis én een prijs ingevuld
    if getattr(event, "is_free", None) and getattr(event, "prijs_indicatie", None):
        uit.append("gemarkeerd als gratis maar met een prijs")
    # binnen én buiten tegelijk als expliciete booleans
    if getattr(event, "indoor", None) is True and getattr(event, "outdoor", None) is True:
        uit.append("zowel binnen als buiten aangevinkt")
    return uit


def zaai_bronstemmen(event, velden=None):
    """Lees de bestaande booleans op een Event in als bronstemmen. Idempotent:
    draai je het twee keer, dan wordt de bestaande bronstem gewoon bijgewerkt.

    Enkel expliciet gezette booleans worden een stem — een veld dat None is
    (onbekend) levert geen stem op, want 'onbekend' is geen 'nee'.
    """
    velden = velden or ZACHTE_VELDEN
    n = 0
    for veld in velden:
        huidig = getattr(event, veld, None)
        if huidig is None:
            continue
        leg_stem_vast(event.id, veld, bool(huidig), family=None)
        n += 1
    return n
