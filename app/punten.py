"""Ravotpas — punten, vosje-niveaus en badges (speels, voor de kinderen).

Ontwerp:
- Punten zijn een LOGBOEK (RavotPunt) met een unieke sleutel per
  (gezin, reden, event): dubbel klikken of spammen levert nooit extra punten op.
- Badges worden LIVE berekend uit bestaande data (bezoeken, reviews, foto's):
  geen extra opslag, geen migratie, altijd consistent.
- De stempelkaart is de verzameling bevestigde bezoeken ("geweest"), elk met
  het type-emoji van de plek — verzamelen zoals kaarten, maar dan écht buiten.
"""
from datetime import datetime, timedelta, time as _time

from .extensions import db
from .models import (Event, Photo, PUNT_REDENEN, RavotPunt, Review,
                     SavedEvent, get_int)
from .types import activiteit_type

# Vosje-niveaus: (ondergrens punten, emoji, naam). De drempels zijn instelbaar
# (niveau_drempels): een titel moet verdiend blijven voelen, dus Vossenkoning
# hoort een meerjarendoel te zijn, geen kwestie van twintig uitstappen.
NIVEAU_NAMEN = [("🐣", "Welpje"), ("🔍", "Speurneus"), ("🦊", "Ravotter"),
                ("⭐", "Supervos"), ("👑", "Vossenkoning")]
STANDAARD_DREMPELS = [0, 100, 400, 1000, 2500]


def niveaus():
    """De ladder zoals ingesteld; valt terug op de standaard bij onzin."""
    from .models import get_setting
    ruw = (get_setting("niveau_drempels") or "").strip()
    grenzen = STANDAARD_DREMPELS
    if ruw:
        try:
            waarden = [int(x) for x in ruw.replace(";", ",").split(",")
                       if x.strip()]
            if len(waarden) == len(NIVEAU_NAMEN) and waarden == sorted(waarden):
                grenzen = waarden
        except ValueError:
            pass
    return [(g, e, n) for g, (e, n) in zip(grenzen, NIVEAU_NAMEN)]


NIVEAUS = [(g, e, n) for g, (e, n) in zip(STANDAARD_DREMPELS, NIVEAU_NAMEN)]

# Badges: (code, emoji, naam, uitleg, doel) — 'teller' wordt live berekend.
BADGES = [
    ("speeltuin", "🛝", "Speeltuinspeurder", "Bezoek 3 speeltuinen", 3),
    ("museum", "🏛️", "Museummuis", "Bezoek 3 musea", 3),
    ("regen", "🌧️", "Regenridder", "Bezoek 3 binnenactiviteiten", 3),
    ("natuur", "🌳", "Natuurvriendje", "Bezoek 3 parken of natuurgebieden", 3),
    ("fotograaf", "📸", "Fotograaf van dienst", "3 goedgekeurde foto's", 3),
    ("recensent", "😄", "Scorekampioen", "Geef 5 Ravotscores", 5),
    ("ontdekker", "🦊", "Echte Ravotter", "Bezoek 10 verschillende plekken", 10),
    ("reiziger", "🗺️", "Vlaanderen-verkenner", "Ravot in 5 verschillende gemeenten", 5),
    ("aanvuller", "🧩", "Fiche-aanvuller", "Vul 10 keer info aan op een fiche", 10),
    ("pionier", "🌟", "Pionier", "Bij de eersten die meebouwden aan Ravot", 1),
]

_BADGE_TYPES = {
    "speeltuin": {"playground"},
    "museum": {"museum"},
    "natuur": {"park", "nature_reserve"},
}


def ken_toe(family_id, reden, ref_id=None):
    """Punten toekennen — stil en idempotent. Retourneert het aantal punten
    (0 als deze actie al eens beloond werd). Commit gebeurt door de caller."""
    # Puntwaarden zijn instelbaar (patch 180); de code-waarde is de standaard.
    punten = get_int(f"punt_{reden}", PUNT_REDENEN.get(reden, 0))
    if punten is None:
        punten = PUNT_REDENEN.get(reden, 0)
    if not family_id or punten <= 0:
        return 0
    ref_id = int(ref_id or 0)
    bestaat = RavotPunt.query.filter_by(family_id=family_id, reden=reden,
                                        ref_id=ref_id).first()
    if bestaat:
        return 0
    # Anti-farming: plafonds per dag. Zonder er te veel woorden aan vuil te
    # maken: boven het plafond gaan de acties gewoon door, maar zonder punten.
    # Een echt gezin bezoekt 1-3 plekken per dag; een klikker honderd.
    vandaag_start = datetime.combine(datetime.utcnow().date(), _time.min)
    q_vandaag = RavotPunt.query.filter(RavotPunt.family_id == family_id,
                                       RavotPunt.created_at >= vandaag_start)
    dag_max = get_int("punten_dag_max", 60) or 60
    if (q_vandaag.with_entities(db.func.coalesce(
            db.func.sum(RavotPunt.punten), 0)).scalar() or 0) + punten > dag_max:
        return 0
    if reden == "geweest":
        g_max = get_int("geweest_dag_max", 3) or 3
        if q_vandaag.filter(RavotPunt.reden == "geweest").count() >= g_max:
            return 0
    if reden == "veld_stem":
        # Veldstemmen zijn de enige bijdrage waarvoor je nergens hoeft te zijn
        # geweest: één klik per voorziening. Zonder eigen plafond kan een gezin
        # eindeloos doorklikken over duizenden plekken (patch 180).
        v_max = get_int("veldstem_dag_max", 8)
        if v_max and q_vandaag.filter(
                RavotPunt.reden == "veld_stem").count() >= v_max:
            return 0
    eerste_keer = not RavotPunt.query.filter_by(family_id=family_id).first()
    db.session.add(RavotPunt(family_id=family_id, reden=reden,
                             ref_id=ref_id, punten=punten))
    if eerste_keer and reden != "uitnodiging":
        from .models import Family
        fam = db.session.get(Family, family_id)
        if fam is not None:
            beloon_uitnodiger(fam)
    return punten


def deelcode(family):
    """Persoonlijke uitnodigingscode (lazy aangemaakt, leesbaar alfabet)."""
    import secrets as _sec
    if family.ref_code:
        return family.ref_code
    from .models import Family
    alfabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(30):
        code = "".join(_sec.choice(alfabet) for _ in range(8))
        if not Family.query.filter_by(ref_code=code).first():
            family.ref_code = code
            db.session.commit()
            return code
    return None


def beloon_uitnodiger(nieuw_gezin):
    """Ken de uitnodiger punten toe zodra het aangebrachte gezin zijn éérste
    eigen punten verdient — dat is het bewijs dat het echt meedoet, en het
    maakt wegwerp-aanmeldingen waardeloos. Idempotent per aangebracht gezin."""
    inviter = getattr(nieuw_gezin, "invited_by", None)
    if not inviter or inviter == nieuw_gezin.id:
        return 0
    return ken_toe(inviter, "uitnodiging", ref_id=nieuw_gezin.id)


def totaal(family_id):
    """Alles wat ooit verdiend werd (som van het grootboek)."""
    return int(db.session.query(db.func.coalesce(db.func.sum(RavotPunt.punten), 0))
               .filter(RavotPunt.family_id == family_id).scalar() or 0)


def niveau_punten(family_id):
    """Basis voor het vosjes-niveau: het hóógste totaal dat dit gezin ooit
    bereikte. Zo houdt Ravot zijn belofte 'je niveau en badges blijven voor
    altijd' — ook na inwisselen of na een correctie door het beheer. Enkel
    een expliciete niveaucorrectie (bij misbruik) kan het verlagen."""
    from .models import Family
    fam = db.session.get(Family, family_id)
    nu = totaal(family_id)
    hoogste = getattr(fam, "niveau_hoogste", None) or 0
    if nu > hoogste and fam is not None:
        fam.niveau_hoogste = nu
        db.session.commit()
        return nu
    return max(nu, hoogste)


def zet_niveau_terug(family_id):
    """Hoogwatermerk gelijkstellen aan het huidige totaal — enkel gebruiken
    bij vastgesteld misbruik."""
    from .models import Family
    fam = db.session.get(Family, family_id)
    if fam is not None:
        fam.niveau_hoogste = max(0, totaal(family_id))
        db.session.commit()


def _uitgegeven(family_id):
    from .models import Inwissel
    return int(db.session.query(
        db.func.coalesce(db.func.sum(Inwissel.punten), 0))
        .filter(Inwissel.family_id == family_id,
                Inwissel.status != "geannuleerd").scalar() or 0)


def _verdiend_sinds(family_id, moment):
    return int(db.session.query(
        db.func.coalesce(db.func.sum(RavotPunt.punten), 0))
        .filter(RavotPunt.family_id == family_id,
                RavotPunt.created_at >= moment).scalar() or 0)


def _cutoff():
    """Punten ouder dan X maanden vervallen (0 = nooit)."""
    maanden = get_int("punten_geldig_maanden", 6) or 0
    if maanden <= 0:
        return None
    return datetime.utcnow() - timedelta(days=maanden * 30)


def saldo(family_id):
    """Te besteden punten. Regels: inwisselen verbruikt de óudste punten
    eerst, en punten ouder dan de geldigheidstermijn vervallen. Daardoor is
    het saldo nooit groter dan wat je recent verdiende: wie de trui wil, moet
    blijven ravotten — het niveau en de badges vervallen nooit."""
    basis = totaal(family_id) - _uitgegeven(family_id)
    cutoff = _cutoff()
    if cutoff is None:
        return max(0, basis)
    return max(0, min(basis, _verdiend_sinds(family_id, cutoff)))


def vervalt_binnenkort(family_id, dagen=30):
    """Hoeveel van het huidige saldo binnen `dagen` vervalt — voor de
    vriendelijke waarschuwing ('wissel op tijd!')."""
    cutoff = _cutoff()
    if cutoff is None:
        return 0
    uitgegeven = _uitgegeven(family_id)
    tot = totaal(family_id)
    al_vervallen = max(0, (tot - _verdiend_sinds(family_id, cutoff)) - uitgegeven)
    straks_grens = cutoff + timedelta(days=dagen)
    straks_oud = tot - _verdiend_sinds(family_id, straks_grens)
    binnenkort = max(0, straks_oud - uitgegeven) - al_vervallen
    return max(0, min(binnenkort, saldo(family_id)))


def niveau(punten):
    """{emoji, naam, punten, volgende, te_gaan, procent} voor de voortgangsbalk."""
    ladder = niveaus()
    huidig = ladder[0]
    volgende = None
    for i, (grens, emoji, naam) in enumerate(ladder):
        if punten >= grens:
            huidig = (grens, emoji, naam)
            volgende = ladder[i + 1] if i + 1 < len(ladder) else None
    uit = {"emoji": huidig[1], "naam": huidig[2], "punten": punten}
    if volgende:
        span = volgende[0] - huidig[0]
        uit["volgende"] = volgende[2]
        uit["te_gaan"] = volgende[0] - punten
        uit["procent"] = min(100, int(100 * (punten - huidig[0]) / max(span, 1)))
    else:
        uit["volgende"] = None
        uit["te_gaan"] = 0
        uit["procent"] = 100
    return uit


def _bezochte_events(family_id):
    return [s.event for s in SavedEvent.query.filter_by(
        family_id=family_id, geweest=True).all() if s.event]


# Pionier: de eerste ~100 gezinnen die ooit een voorziening bijdroegen. Tijdelijk
# en onherhaalbaar — wie te laat is, kan hem nooit meer halen. Dat maakt hem
# begeerd en zet vroege gebruikers aan om nú te helpen.
PIONIER_AANTAL = 100
_pionier_cache = None


def _is_pionier(family_id_str):
    """Hoort dit gezin bij de eerste PIONIER_AANTAL bijdragers? Bepaald op de
    vroegste bijdrage per stemmer, chronologisch. Gecachet per proces."""
    global _pionier_cache
    if _pionier_cache is None:
        from .models import VeldStem
        # vroegste bijdrage per gebruiker (bron uitgesloten)
        rijen = (VeldStem.query
                 .filter(VeldStem.stemmer != "bron")
                 .with_entities(VeldStem.stemmer,
                                db.func.min(VeldStem.created_at).label("eerste"))
                 .group_by(VeldStem.stemmer)
                 .order_by(db.text("eerste ASC"))
                 .limit(PIONIER_AANTAL).all())
        _pionier_cache = {r[0] for r in rijen}
    return family_id_str in _pionier_cache


def stempelkaart(family_id):
    """De verzameling: elke bevestigde plek als stempel {emoji, titel, slug,
    gemeente}. Nieuwste eerst — dat voelt als 'kaarten verzamelen'."""
    stempels = []
    for ev in _bezochte_events(family_id):
        t = activiteit_type(ev)
        stempels.append({"emoji": t["emoji"], "type": t["label"],
                         "titel": ev.title, "slug": ev.slug,
                         "gemeente": ev.gemeente})
    return list(reversed(stempels))


def badges(family_id):
    """Alle badges met live voortgang: [{emoji, naam, uitleg, teller, doel,
    behaald}]. Ook niet-behaalde tonen we ('nog 2 te gaan') — dat motiveert."""
    events = _bezochte_events(family_id)
    types = [activiteit_type(e)["code"] for e in events]
    fotos = Photo.query.filter_by(family_id=family_id, status="approved").count()
    reviews = Review.query.filter_by(family_id=family_id).count()
    gemeenten = {e.gemeente for e in events if e.gemeente}
    # Bijdrage-tellers (fase 4): aantal voorziening-bijdragen van dit gezin, en
    # of het gezin tot de eerste ~100 bijdragers hoort (Pionier).
    from .models import VeldStem
    fid = str(family_id)
    aanvullingen = VeldStem.query.filter_by(stemmer=fid).count()
    is_pionier = _is_pionier(fid) if aanvullingen else False
    uit = []
    for code, emoji, naam, uitleg, doel in BADGES:
        if code in _BADGE_TYPES:
            teller = sum(1 for t in types if t in _BADGE_TYPES[code])
        elif code == "regen":
            teller = sum(1 for e in events if e.indoor)
        elif code == "fotograaf":
            teller = fotos
        elif code == "recensent":
            teller = reviews
        elif code == "ontdekker":
            teller = len({e.id for e in events})
        elif code == "reiziger":
            teller = len(gemeenten)
        elif code == "aanvuller":
            teller = aanvullingen
        elif code == "pionier":
            teller = 1 if is_pionier else 0
        else:
            teller = 0
        uit.append({"emoji": emoji, "naam": naam, "uitleg": uitleg,
                    "teller": min(teller, doel), "doel": doel,
                    "behaald": teller >= doel})
    uit.sort(key=lambda b: (not b["behaald"], b["doel"] - b["teller"]))
    return uit
