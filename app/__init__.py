import secrets

import click
from flask import Flask, render_template

from .config import Config
from .extensions import csrf, db, limiter


def create_app(config_object=Config):
    app = Flask(__name__)
    # Achter de Coolify reverse proxy: neem het echte client-IP en het https-
    # schema uit de X-Forwarded-headers over. Zonder dit ziet de rate-limiter
    # álle bezoekers als één IP (het proxy-IP) en werken limieten averechts.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.from_object(config_object)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from .media import poi_image, has_echte_foto
    import os as _os
    from flask import url_for as _url_for
    def static_v(filename):
        """Static-URL met versie-stempel (?v=mtime) zodat caches nooit een
        verouderd bestand serveren na een deploy."""
        try:
            pad = _os.path.join(app.static_folder, filename)
            v = int(_os.path.getmtime(pad))
        except OSError:
            v = 0
        return _url_for("static", filename=filename, v=v)
    app.jinja_env.globals["static_v"] = static_v
    app.jinja_env.globals["poi_image"] = poi_image
    from .media import poi_emoji
    app.jinja_env.globals["poi_emoji"] = poi_emoji
    app.jinja_env.globals["has_echte_foto"] = has_echte_foto
    from .routes.public import event_datum
    app.jinja_env.globals["event_datum"] = event_datum
    from .plaatsen import land_label
    app.jinja_env.globals["land_label"] = land_label
    from .services.label import label_info, kamp_thumb, kamp_fotos
    app.jinja_env.globals["label_info"] = label_info
    app.jinja_env.globals["kamp_thumb"] = kamp_thumb
    app.jinja_env.globals["kamp_fotos"] = kamp_fotos
    from .services.openingsuren import (status_badge, uren_overzicht,
                                        heeft_uren, dag_blokken)
    app.jinja_env.globals["open_badge"] = status_badge
    app.jinja_env.globals["uren_overzicht"] = uren_overzicht
    app.jinja_env.globals["heeft_uren"] = heeft_uren
    app.jinja_env.globals["dag_blokken"] = dag_blokken
    KEUKEN_NL = {"friture": "frituur", "fries": "frituur", "chips": "frituur",
                 "ice_cream": "ijssalon", "pizza": "pizzeria",
                 "pancake": "pannenkoekenhuis", "sandwich": "broodjeszaak",
                 "burger": "burgerzaak", "pasta": "pastazaak",
                 "regional": "streekkeuken", "italian": "Italiaans",
                 "asian": "Aziatisch", "greek": "Grieks", "turkish": "Turks",
                 "kebab": "kebabzaak", "seafood": "viszaak", "tea": "theehuis",
                 "coffee_shop": "koffiebar", "breakfast": "ontbijtzaak"}
    app.jinja_env.globals["keuken_label"] = \
        lambda c: KEUKEN_NL.get((c or "").lower(), (c or "").replace("_", " "))
    from .models import KAMP_THEMAS, KAMP_TALEN
    app.jinja_env.globals["kamp_themas"] = KAMP_THEMAS
    app.jinja_env.globals["kamp_talen"] = KAMP_TALEN
    from .types import activiteit_type
    app.jinja_env.globals["activiteit_type"] = activiteit_type
    from .types import is_commercieel
    app.jinja_env.globals["is_commercieel"] = is_commercieel
    from .routes.public import score_zichtbaar
    app.jinja_env.globals["score_zichtbaar"] = score_zichtbaar

    @app.context_processor
    def _inject_nu():
        from datetime import datetime
        return {"nu_utc": datetime.utcnow()}

    from .routes.public import bp as public_bp
    from .routes.auth import bp as auth_bp
    from .routes.account import bp as account_bp
    from .routes.admin import bp as admin_bp
    from .routes.uitbater import bp as uitbater_bp
    # let op: public als laatste registreren — die heeft de catch-all /<gemeente>
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(uitbater_bp)
    app.register_blueprint(public_bp)

    # Welke bronnen zijn actief? Stuurt de publieke bronvermeldingen. Publiq
    # (UiT/UiTinVlaanderen/Vlieg) verschijnt ENKEL als bron_uit_aan aanstaat.
    BRON_INFO = {
        "uit": ("UiTdatabank", "https://www.uitdatabank.be"),
        "osm": ("OpenStreetMap-bijdragers", "https://www.openstreetmap.org/copyright"),
        "ov": ("Overture Maps", "https://overturemaps.org"),
    }

    @app.context_processor
    def inject_bronnen():
        from .models import get_bool
        try:
            actief = {k: get_bool(f"bron_{k}_aan") for k in ("uit", "osm")}
            actief["ov"] = True    # Overture: attributie zolang er data van is
        except Exception:
            actief = {k: False for k in ("uit", "osm", "ov")}
        verm = [{"code": k, "naam": BRON_INFO[k][0], "url": BRON_INFO[k][1]}
                for k in ("uit", "osm", "ov") if actief.get(k)]
        return {"uit_actief": actief.get("uit", False),
                "bron_actief": actief, "bron_vermeldingen": verm}

    @app.context_processor
    def inject_guest():
        from flask import session
        g = session.get("guest") or {}
        from .vakantie import vakantiecontext
        from .models import get_bool
        ctx = {"guest": g, "has_guest": bool(g.get("postcode")),
               "vakantie": vakantiecontext(), "bewaard_ids": set(),
               "geliked_ids": set(), "weggeklikt_ids": set(),
               "feestjes_aan": get_bool("feestjes_aan"),
               "kampen_aan": get_bool("kampen_aan"),
               "label_aan": get_bool("label_aan")}
        # Welke events heeft dit gezin bewaard? (voor de hartjes op de kaarten)
        fid = session.get("family_id")
        if fid:
            from .models import SavedEvent, Interaction
            try:
                ctx["bewaard_ids"] = {s.event_id for s in
                                      SavedEvent.query.filter_by(family_id=fid)}
                ctx["geliked_ids"] = {i.event_id for i in
                    Interaction.query.filter_by(family_id=fid, type="like")}
                ctx["weggeklikt_ids"] = {i.event_id for i in
                    Interaction.query.filter_by(family_id=fid, type="dismiss")}
            except Exception:
                pass
        # Login-status van uitbater en admin, zodat de navigatiebalk de juiste
        # knoppen toont (i.p.v. altijd "Aanmelden" ook als je al ingelogd bent).
        ctx["is_uitbater"] = bool(session.get("operator_id"))
        ctx["is_admin"] = bool(session.get("admin_id") and session.get("admin_2fa_ok"))
        return ctx

    # -- Security headers (strategienota §8.2) -------------------------------
    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "script-src 'self' https://unpkg.com; "
            "frame-ancestors 'none'",
        )
        if not app.debug and not app.testing:
            resp.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
        return resp

    # -- Onderhoudsmodus (instelling 'onderhoud_aan' in /beheer) -------------
    @app.before_request
    def onderhoudsmodus():
        from flask import request, session
        # Beheer en statische bestanden blijven altijd bereikbaar,
        # anders kan je de modus niet meer uitzetten.
        pad = request.path
        if pad.startswith("/beheer") or pad.startswith("/static"):
            return None
        # /health blijft altijd antwoorden: anders ziet de Coolify-healthcheck
        # de onderhoudsmodus als een kapotte app en gaat die herstarten.
        if pad == "/health":
            return None
        # Ingelogde admins mogen de site gewoon bekijken (preview).
        if session.get("admin_id") and session.get("admin_2fa_ok"):
            return None
        from .models import get_bool
        if get_bool("onderhoud_aan"):
            # 503 + Retry-After: zoekmachines weten dat dit tijdelijk is
            # en de-indexeren niets.
            resp = app.make_response(
                render_template("public/onderhoud.html"))
            resp.status_code = 503
            resp.headers["Retry-After"] = "3600"
            resp.headers["Cache-Control"] = "no-store"
            return resp
        return None

    @app.errorhandler(429)
    def te_veel(_):
        return render_template("429.html"), 429

    @app.errorhandler(413)
    def te_groot(_):
        """Upload boven MAX_CONTENT_LENGTH: vriendelijk terugsturen i.p.v. een
        kale foutpagina. (In de praktijk zeldzaam: de browser verkleint foto's
        al vóór het uploaden en de server heringcodeert sowieso.)"""
        from flask import flash, redirect, request
        mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
        flash(f"Dat bestand is te groot (max. {mb} MB). Tip: een foto rechtstreeks "
              "uit je galerij werkt meestal wél — wij verkleinen ze automatisch.",
              "error")
        return redirect(request.referrer or "/")

    @app.errorhandler(404)
    def not_found(_):
        return render_template("public/404.html", family=None, active=None,
                               title="Niet gevonden"), 404

    @app.errorhandler(500)
    def server_error(_):
        # nooit interne details tonen (strategienota §8.2)
        return render_template("public/500.html", family=None, active=None,
                               title="Er ging iets mis"), 500

    register_cli(app)
    return app


# ------------------------------------------------------------------ CLI-jobs --

def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Tabellen aanmaken."""
        db.create_all()
        click.echo("Database klaar.")

    @app.cli.command("laad-postcodes")
    def laad_postcodes():
        """Vul ontbrekende postcode-zwaartepunten aan uit de ingebakken
        Vlaamse+Brusselse lijst (app/data/postcodes_vl.json). Bestaande
        zwaartepunten (afgeleid uit echte events) blijven ongemoeid."""
        import json
        import os
        from .models import PostcodeCentroid
        pad = os.path.join(os.path.dirname(__file__), "data", "postcodes_vl.json")
        lijst = json.load(open(pad))
        bestaand = {c.postcode for c in PostcodeCentroid.query.all()}
        nieuw = 0
        for pc, info in lijst.items():
            if pc in bestaand:
                continue
            db.session.add(PostcodeCentroid(postcode=pc, gemeente=info["gemeente"],
                                            lat=info["lat"], lng=info["lng"]))
            nieuw += 1
        db.session.commit()
        print(f"Zwaartepunten: {len(bestaand)} bestaand behouden · {nieuw} toegevoegd "
              f"· totaal {len(bestaand) + nieuw}")

    @app.cli.command("opruim-buitenland")
    @click.option("--ja", is_flag=True, help="Effectief verwijderen (anders enkel tellen).")
    def opruim_buitenland(ja):
        """Verwijder OSM-plekken ver buiten Vlaanderen+Brussel (Nederland,
        Duitsland, ...). Zonder --ja wordt enkel geteld. Plekken waar een
        gezin of uitbater iets aan koppelde (review, foto, claim, ...)
        worden altijd overgeslagen."""
        from sqlalchemy import or_, text
        from .models import Event
        # Vlaanderen + Brussel met marge (grensstreek blijft bewust binnen)
        Z, W, N, O = 50.60, 2.45, 51.60, 6.00
        buiten = Event.query.filter(
            Event.source == "osm",
            Event.lat.isnot(None), Event.lng.isnot(None),
            or_(Event.lat < Z, Event.lat > N, Event.lng < W, Event.lng > O))
        totaal = buiten.count()
        vrij = buiten
        for tabel in ("interactions", "saved_events", "reviews", "shares",
                      "reports", "photos", "enrich_proposals", "operator_claims",
                      "edit_proposals", "partner_payments", "daguitstap_items",
                      "feestje_aanvragen"):
            vrij = vrij.filter(text(
                f"NOT EXISTS (SELECT 1 FROM {tabel} "
                f"WHERE {tabel}.event_id = events.id)"))
        vrij_n = vrij.count()
        print(f"Buiten Vlaanderen+Brussel: {totaal} OSM-plekken "
              f"({vrij_n} zonder koppelingen, {totaal - vrij_n} met — die blijven).")
        if not ja:
            print("Niets verwijderd. Draai met --ja om effectief op te ruimen.")
            return
        ids = [r.id for r in vrij.with_entities(Event.id).all()]
        weg = 0
        for i in range(0, len(ids), 1000):
            stuk = ids[i:i + 1000]
            db.session.execute(Event.__table__.delete()
                               .where(Event.id.in_(stuk)))
            db.session.commit()
            weg += len(stuk)
            print(f"  {weg} van {len(ids)} verwijderd...", flush=True)
        print(f"Klaar: {weg} buitenlandse plekken verwijderd.")

    @app.cli.command("backfill-gemeenten")
    def backfill_gemeenten():
        """Vul gemeente/postcode aan voor plekken zonder adres (OSM-punten),
        via het dichtstbijzijnde postcode-zwaartepunt. Veilig herhaalbaar.
        Leest eerst alle kandidaten in en schrijft daarna in stukken — een
        open leescursor combineren met commits brak op Postgres."""
        from sqlalchemy import update as sa_update, func as sa_func
        from .models import Event
        from .services.sources.base import dichtste_gemeente
        kandidaten = (Event.query
                      .filter(Event.gemeente.is_(None),
                              Event.lat.isnot(None), Event.lng.isnot(None))
                      .with_entities(Event.id, Event.lat, Event.lng).all())
        totaal = len(kandidaten)
        gevuld = zonder = 0
        for i, (eid, lat, lng) in enumerate(kandidaten, 1):
            g, p = dichtste_gemeente(lat, lng)
            if g:
                db.session.execute(
                    sa_update(Event).where(Event.id == eid)
                    .values(gemeente=g,
                            postcode=sa_func.coalesce(Event.postcode, p)))
                gevuld += 1
            else:
                zonder += 1
            if i % 2000 == 0:
                db.session.commit()   # in stukken: veilig herstartbaar
                print(f"  {i} van {totaal}...", flush=True)
        db.session.commit()
        print(f"Gemeente aangevuld: {gevuld} · geen zwaartepunt binnen 10 km: {zonder}")

    @app.cli.command("backfill-openingsuren")
    def backfill_openingsuren():
        """Eenmalig: 'Openingsuren: ...' uit beschrijvingen halen en omzetten
        naar het gestructureerde openingsuren-veld (urentabel + open/dicht-badge
        op de fiche). Veilig herhaalbaar; rapporteert de aantallen."""
        import re
        from .models import Event
        from .services.openingsuren import parse_osm_uren
        patroon = re.compile(r"\s*Openingsuren:\s*([^\n]+?)\s*$")
        gevuld = gestript = onleesbaar = omschreven = 0
        for ev in Event.query.filter(Event.description.contains("Openingsuren:")).all():
            m = patroon.search(ev.description or "")
            if not m:
                continue
            spec = m.group(1).strip()
            laag = spec.lower()

            # Betekenisvolle notaties zonder klokuren -> vriendelijke NL-zin
            zin = None
            if any(w in laag for w in ("sunrise", "sunset", "dawn", "dusk",
                                       "einbruch der dunkelheit")):
                zin = "Vrij toegankelijk van zonsopgang tot zonsondergang."
            elif "afspraak" in laag or "appointment" in laag:
                zin = "Enkel op afspraak."
            if zin:
                ev.description = (patroon.sub("", ev.description).strip()
                                  + " " + zin).strip()
                omschreven += 1
                continue
            # "off"/"closed" zonder meer: geen informatiewaarde -> gewoon weg
            if laag.strip("'\" ") in ("off", "closed"):
                ev.description = patroon.sub("", ev.description).strip()
                gestript += 1
                continue

            if not ev.openingsuren:
                uren = parse_osm_uren(spec)
                if uren:
                    ev.openingsuren = uren
                    gevuld += 1
                else:
                    onleesbaar += 1
                    continue   # tekst laten staan: beter ruw dan niets
            ev.description = patroon.sub("", ev.description).strip()
            gestript += 1
        db.session.commit()
        totaal = Event.query.filter(Event.openingsuren.isnot(None)).count()
        print(f"Gestructureerd gevuld: {gevuld} · tekst opgekuist: {gestript} "
              f"· omschreven (daglicht/afspraak): {omschreven} "
              f"· onleesbaar gelaten: {onleesbaar} · totaal plekken met uren: {totaal}")

    @app.cli.command("migrate-db")
    def migrate_db():
        """Voegt ontbrekende kolommen/tabellen toe zonder data te wissen."""
        from sqlalchemy import inspect, text
        db.create_all()   # eerst: maakt ontbrekende tabellen (ook op een verse DB)
        insp = inspect(db.engine)
        added = []
        cols = {c["name"] for c in insp.get_columns("events")}
        if "has_vlieg" not in cols:
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN has_vlieg BOOLEAN DEFAULT FALSE"))
            added.append("events.has_vlieg")
        # Meerdere bronnen: externe id, canonieke link, bronvermelding, POI-vlag.
        # Additief en veilig — bestaande UiT-rijen blijven ongewijzigd.
        for kol, ddl in (
            ("ext_id", "ALTER TABLE events ADD COLUMN ext_id VARCHAR(120)"),
            ("source_url", "ALTER TABLE events ADD COLUMN source_url VARCHAR(500)"),
            ("attribution", "ALTER TABLE events ADD COLUMN attribution VARCHAR(120)"),
            ("is_permanent",
             "ALTER TABLE events ADD COLUMN is_permanent BOOLEAN DEFAULT FALSE NOT NULL"),
            ("hidden",
             "ALTER TABLE events ADD COLUMN hidden BOOLEAN DEFAULT FALSE NOT NULL"),
            ("dupe_of", "ALTER TABLE events ADD COLUMN dupe_of INTEGER"),
            ("adres", "ALTER TABLE events ADD COLUMN adres VARCHAR(255)"),
            ("pending",
             "ALTER TABLE events ADD COLUMN pending BOOLEAN DEFAULT FALSE NOT NULL"),
            ("submitted_by", "ALTER TABLE events ADD COLUMN submitted_by INTEGER"),
            ("partner_until", "ALTER TABLE events ADD COLUMN partner_until TIMESTAMP"),
            ("quality", "ALTER TABLE events ADD COLUMN quality INTEGER"),
            ("subtype", "ALTER TABLE events ADD COLUMN subtype VARCHAR(40)"),
            ("curated", "ALTER TABLE events ADD COLUMN curated BOOLEAN DEFAULT FALSE"),
            ("curated_by", "ALTER TABLE events ADD COLUMN curated_by INTEGER"),
            ("curated_at", "ALTER TABLE events ADD COLUMN curated_at TIMESTAMP"),
        ):
            if kol not in cols:
                db.session.execute(text(ddl))
                added.append(f"events.{kol}")
        # index op (source, ext_id) voor snelle upserts — genegeerd als hij al bestaat
        try:
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_events_source_ext "
                "ON events (source, ext_id)"))
        except Exception:
            pass
        # totp_confirmed op admins: bestaande admins op FALSE → doorlopen QR-flow
        admin_cols = {c["name"] for c in insp.get_columns("admins")}
        if "totp_confirmed" not in admin_cols:
            db.session.execute(text(
                "ALTER TABLE admins ADD COLUMN totp_confirmed BOOLEAN DEFAULT FALSE NOT NULL"))
            added.append("admins.totp_confirmed")
        if "role" not in admin_cols:
            db.session.execute(text(
                "ALTER TABLE admins ADD COLUMN role VARCHAR(12) DEFAULT 'admin' NOT NULL"))
            added.append("admins.role")
        # attempts op magic_tokens: brute-force-slot voor inlogcodes
        mt_cols = {c["name"] for c in insp.get_columns("magic_tokens")}
        if "attempts" not in mt_cols:
            db.session.execute(text(
                "ALTER TABLE magic_tokens ADD COLUMN attempts INTEGER DEFAULT 0 NOT NULL"))
            added.append("magic_tokens.attempts")
        # Karakter-schuifjes op reviews (nieuwe Ravotscore)
        rev_cols = {c["name"] for c in insp.get_columns("reviews")}
        for kol in ("sfeer_rustig_actief", "sfeer_prijs", "sfeer_leeftijd"):
            if kol not in rev_cols:
                db.session.execute(text(f"ALTER TABLE reviews ADD COLUMN {kol} INTEGER"))
                added.append(f"reviews.{kol}")
        # SavedEvent: intentie- en geweest-status (voor 'heen willen' → 'geweest?')
        se_cols = {c["name"] for c in insp.get_columns("saved_events")}
        for kol, default in (("wil_heen", "TRUE"), ("geweest", "FALSE"),
                             ("gevraagd_geweest", "FALSE")):
            if kol not in se_cols:
                db.session.execute(text(
                    f"ALTER TABLE saved_events ADD COLUMN {kol} BOOLEAN DEFAULT {default} NOT NULL"))
                added.append(f"saved_events.{kol}")
        # Connection: wederzijds akkoord (status + requested_by)
        conn_cols = {c["name"] for c in insp.get_columns("connections")}
        if "requested_by" not in conn_cols:
            db.session.execute(text("ALTER TABLE connections ADD COLUMN requested_by INTEGER"))
            added.append("connections.requested_by")
        # Family: active-vlag (admin kan deactiveren)
        fam_cols = {c["name"] for c in insp.get_columns("families")}
        if "active" not in fam_cols:
            db.session.execute(text(
                "ALTER TABLE families ADD COLUMN active BOOLEAN DEFAULT TRUE NOT NULL"))
            added.append("families.active")
        hk_cols = {c["name"] for c in insp.get_columns("horeca_kandidaten")} \
            if insp.has_table("horeca_kandidaten") else set()
        if hk_cols and "ai_advies" not in hk_cols:
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN ai_advies VARCHAR(8)"))
            added.append("horeca_kandidaten.ai_advies")
        fj_cols = {c["name"] for c in insp.get_columns("feestjes")} \
            if insp.has_table("feestjes") else set()
        if fj_cols and "aanleiding" not in fj_cols:
            db.session.execute(text(
                "ALTER TABLE feestjes ADD COLUMN aanleiding VARCHAR(12) "
                "DEFAULT 'verjaardag' NOT NULL"))
            added.append("feestjes.aanleiding")
        if hk_cols and "telefoon" not in hk_cols:
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN telefoon VARCHAR(40)"))
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN email VARCHAR(255)"))
            added.append("horeca_kandidaten.telefoon + email")
        if hk_cols and "doel" not in hk_cols:
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN doel VARCHAR(8) "
                "DEFAULT 'gezin'"))
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN is_feest BOOLEAN "
                "DEFAULT FALSE"))
            added.append("horeca_kandidaten.doel + is_feest")
        if hk_cols and "ai_uitleg" not in hk_cols:
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN ai_uitleg VARCHAR(200)"))
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN gesloten "
                "BOOLEAN DEFAULT FALSE"))
            added.append("horeca_kandidaten.ai_uitleg + gesloten")
        if hk_cols and "winterbar_hint" not in hk_cols:
            db.session.execute(text(
                "ALTER TABLE horeca_kandidaten ADD COLUMN winterbar_hint "
                "BOOLEAN DEFAULT FALSE"))
            added.append("horeca_kandidaten.winterbar_hint")
        ev_cols = {c["name"] for c in insp.get_columns("events")} \
            if insp.has_table("events") else set()
        # Kamp-kolommen per stuk idempotent (ADD COLUMN IF NOT EXISTS): zo is
        # een half-gelopen migratie geen probleem.
        _kamp_kolommen = [
            "is_kamp BOOLEAN DEFAULT FALSE", "kamp_start DATE", "kamp_eind DATE",
            "kamp_inschrijf_url VARCHAR(500)", "kamp_prijs VARCHAR(40)",
            "kamp_organisator VARCHAR(200)", "kamp_opvang BOOLEAN DEFAULT FALSE",
            "kamp_maaltijd BOOLEAN DEFAULT FALSE", "kamp_fiscaal BOOLEAN DEFAULT FALSE",
            "kamp_mutualiteit BOOLEAN DEFAULT FALSE",
            "kamp_overnachting BOOLEAN DEFAULT FALSE",
            "kamp_thema VARCHAR(40)", "kamp_taal VARCHAR(40)",
        ]
        # Ruim een eventueel eerder (fout) aangemaakte kleinletter-kolom op.
        if ev_cols and "kamp_vozorg" in ev_cols:
            db.session.execute(text("ALTER TABLE events DROP COLUMN IF EXISTS kamp_vozorg"))
            added.append("events.kamp_vozorg opgeruimd")
        _kamp_nieuw = False
        for coldef in _kamp_kolommen:
            kol = coldef.split()[0]
            if kol not in (ev_cols or []):
                db.session.execute(text(
                    f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {coldef}"))
                _kamp_nieuw = True
        if _kamp_nieuw:
            added.append("events.kamp-velden")
        # Uitgebreide voorzieningen + openingsuren (idempotent per kolom).
        _extra_ev = [
            "terras BOOLEAN", "overdekt_terras BOOLEAN", "parking BOOLEAN",
            "toegankelijk BOOLEAN", "allergievriendelijk BOOLEAN",
            "babyvoeding BOOLEAN", "huisdieren BOOLEAN", "openingsuren JSON",
            # patch 101: contact + praktische voorzieningen uit OSM/Overture
            "email VARCHAR(255)", "socials JSON", "toilet BOOLEAN",
            "drinkwater BOOLEAN", "picknick BOOLEAN", "bbq BOOLEAN",
            "speeltoestellen JSON",
            # patch 102: horeca-detail + uitbater
            "cuisine VARCHAR(80)", "veggie BOOLEAN", "afhaal BOOLEAN",
            "reserveren BOOLEAN", "uitbater_naam VARCHAR(120)",
        ]
        _extra_nieuw = False
        for coldef in _extra_ev:
            kol = coldef.split()[0]
            if kol not in (ev_cols or []):
                db.session.execute(text(
                    f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {coldef}"))
                _extra_nieuw = True
        for kd in ("socials JSON", "brand VARCHAR(80)", "confidence REAL"):
            db.session.execute(text(
                f"ALTER TABLE horeca_kandidaten ADD COLUMN IF NOT EXISTS {kd}"))
        if _extra_nieuw:
            added.append("events.voorzieningen + openingsuren")
        if ev_cols and "reservatie_url" not in ev_cols:
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS reservatie_url VARCHAR(500)"))
            added.append("events.reservatie_url")
        if ev_cols and "feest_min_pers" not in ev_cols:
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS feest_min_pers INTEGER"))
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS feest_max_pers INTEGER"))
            added.append("events.feest_min_pers + feest_max_pers")
        _op_cols = {c["name"] for c in insp.get_columns("operators")} \
            if insp.has_table("operators") else set()
        for _kol, _def in [("contactpersoon", "VARCHAR(120)"),
                           ("factuur_email", "VARCHAR(255)"),
                           ("telefoon", "VARCHAR(40)")]:
            if _op_cols and _kol not in _op_cols:
                db.session.execute(text(
                    f"ALTER TABLE operators ADD COLUMN IF NOT EXISTS {_kol} {_def}"))
                added.append(f"operators.{_kol}")
        _photo_cols = {c["name"] for c in insp.get_columns("photos")} \
            if insp.has_table("photos") else set()
        if _photo_cols and "weiger_reden" not in _photo_cols:
            db.session.execute(text(
                "ALTER TABLE photos ADD COLUMN IF NOT EXISTS weiger_reden VARCHAR(60)"))
            added.append("photos.weiger_reden")
        # Handmatige (gratis) partner heeft geen betalende operator: constraint lossen.
        if insp.has_table("partner_payments"):
            try:
                db.session.execute(text(
                    "ALTER TABLE partner_payments ALTER COLUMN operator_id DROP NOT NULL"))
                added.append("partner_payments.operator_id nullable")
            except Exception:
                db.session.rollback()   # SQLite/al gebeurd: negeren
            # plan-kolom verbreden: 'handmatig' (9) past niet in de oude VARCHAR(8).
            try:
                db.session.execute(text(
                    "ALTER TABLE partner_payments ALTER COLUMN plan TYPE VARCHAR(16)"))
                added.append("partner_payments.plan -> VARCHAR(16)")
            except Exception:
                db.session.rollback()   # SQLite/al gebeurd: negeren
        # Claim-verificatie: domein-match-vlag.
        if insp.has_table("operator_claims"):
            oc_cols = {c["name"] for c in insp.get_columns("operator_claims")}
            if "domein_match" not in oc_cols:
                db.session.execute(text(
                    "ALTER TABLE operator_claims ADD COLUMN IF NOT EXISTS "
                    "domein_match BOOLEAN DEFAULT FALSE"))
                added.append("operator_claims.domein_match")
        if ev_cols and "label_niveau" not in ev_cols:
            db.session.execute(text("ALTER TABLE events ADD COLUMN label_niveau INTEGER DEFAULT 0"))
            db.session.execute(text("ALTER TABLE events ADD COLUMN label_jaar INTEGER"))
            added.append("events.label_niveau + label_jaar")
        if ev_cols and "in_feestlijst" not in ev_cols:
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN in_feestlijst BOOLEAN DEFAULT FALSE"))
            added.append("events.in_feestlijst")
        if ev_cols and "telefoon" not in ev_cols:
            db.session.execute(text("ALTER TABLE events ADD COLUMN telefoon VARCHAR(40)"))
            added.append("events.telefoon")
        if ev_cols and "nagekeken" not in ev_cols:
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN nagekeken BOOLEAN "
                "DEFAULT FALSE NOT NULL"))
            added.append("events.nagekeken")
        for kol in ("kinderstoel", "speelhoek", "kindermenu"):
            if ev_cols and kol not in ev_cols:
                db.session.execute(text(f"ALTER TABLE events ADD COLUMN {kol} BOOLEAN"))
                added.append(f"events.{kol}")
        iw_cols = {c["name"] for c in insp.get_columns("inwisselingen")} \
            if insp.has_table("inwisselingen") else set()
        if iw_cols and "bezorg_adres" not in iw_cols:
            db.session.execute(text(
                "ALTER TABLE inwisselingen ADD COLUMN bezorg_adres VARCHAR(300)"))
            added.append("inwisselingen.bezorg_adres")
        ph_cols = {c["name"] for c in insp.get_columns("photos")} \
            if insp.has_table("photos") else set()
        if ph_cols and "soort" not in ph_cols:
            db.session.execute(text(
                "ALTER TABLE photos ADD COLUMN soort VARCHAR(12) "
                "DEFAULT 'gezin' NOT NULL"))
            added.append("photos.soort")
        # Gezinsplekken die vóór deze fix werden goedgekeurd: alsnog cureren
        # en een kwaliteitsscore geven, anders blijven ze van de kaart vallen.
        # Eerst alle schema-wijzigingen vastleggen, zodat de ORM-query hieronder
        # zeker met een bijgewerkte tabel werkt (voorkomt UndefinedColumn).
        db.session.commit()
        from .kwaliteit import bereken_kwaliteit
        from .models import Event as _Ev
        for _e in _Ev.query.filter(_Ev.source == "user",
                                   _Ev.pending.is_(False)).all():
            gewijzigd = False
            if not _e.curated:
                _e.curated = True
                gewijzigd = True
            if _e.quality is None:
                _e.quality = bereken_kwaliteit(_e, heeft_reviews=False)
                gewijzigd = True
            if gewijzigd:
                added.append(f"gezinsplek #{_e.id} gerepareerd")
        # Overture in de bronnenlijst: beginstatus afleiden uit de voorraad.
        from .models import HorecaKandidaat, SyncStatus
        if insp.has_table("horeca_kandidaten") and insp.has_table("sync_status") \
                and db.session.get(SyncStatus, "overture") is None:
            n_kand = HorecaKandidaat.query.count()
            if n_kand:
                laatste = db.session.query(
                    db.func.max(HorecaKandidaat.created_at)).scalar()
                st = SyncStatus(source="overture", state="done",
                                last_result=f"{n_kand} kandidaten in voorraad")
                st.last_run = laatste
                db.session.add(st)
                added.append("bronstatus overture")
        # Beloningscatalogus: eenmalige startvoorraad (punten = euro x 20).
        from .models import Beloning
        if Beloning.query.count() == 0:
            for emoji, naam, beschr, eur, pt in (
                ("🦊", "Stickervel het vosje", "Vel met 6 vosje-stickers", 1.5, 30),
                ("🎈", "Ravot-ballonnen (5 stuks)", "Voor het volgende feestje", 2, 40),
                ("☂️", "Ravot-paraplu", "Regenridders blijven droog", 8, 160),
                ("👕", "Ravot-T-shirt (kindermaat)", "Oranje, met het vosje", 12, 240),
                ("🧥", "Ravot-trui", "Voor de échte Vossenkoning", 25, 500),
            ):
                db.session.add(Beloning(emoji=emoji, naam=naam, beschrijving=beschr,
                                        waarde_eur=eur, punten=pt, soort="ravot"))
            added.append("beloningscatalogus (5 items)")
        # Woordgebruik: oude review-tags herschrijven naar de nieuwe bewoording
        # (wandelwagen i.p.v. buggy, afgesloten speelterrein i.p.v. omheind).
        from .models import OUDE_TAGS, Review
        for rv in Review.query.all():
            tags = rv.tags or []
            nieuw = [OUDE_TAGS.get(t, t) for t in tags]
            if nieuw != tags:
                rv.tags = nieuw
                added.append(f"review #{rv.id}: tags vernieuwd")
        # Overstap naar geboortejaren: bestaande gezinnen krijgen FALSE en zien
        # één keer de vraag om hun gegevens na te kijken.
        if "gegevens_nagekeken" not in fam_cols:
            db.session.execute(text(
                "ALTER TABLE families ADD COLUMN gegevens_nagekeken "
                "BOOLEAN DEFAULT FALSE NOT NULL"))
            added.append("families.gegevens_nagekeken")
        # Ouder-filters + feestjes op events (grote ronde 2026-07)
        cols = {c["name"] for c in insp.get_columns("events")}
        for kol, ddl in (
            ("omheind", "ALTER TABLE events ADD COLUMN omheind BOOLEAN"),
            ("verzorgingstafel", "ALTER TABLE events ADD COLUMN verzorgingstafel BOOLEAN"),
            ("buggy_ok", "ALTER TABLE events ADD COLUMN buggy_ok BOOLEAN"),
            ("feest", "ALTER TABLE events ADD COLUMN feest BOOLEAN DEFAULT FALSE NOT NULL"),
            ("feest_soorten", "ALTER TABLE events ADD COLUMN feest_soorten JSON"),
            ("feest_contact", "ALTER TABLE events ADD COLUMN feest_contact VARCHAR(255)"),
        ):
            if kol not in cols:
                db.session.execute(text(ddl))
                added.append(f"events.{kol}")
        # settings-tabel (nieuw in admin-config) — create_all maakt enkel wat ontbreekt
        db.create_all()  # maakt ook friend_invites-tabel aan
        if "settings" not in insp.get_table_names():
            added.append("settings (tabel)")
        if "friend_invites" not in insp.get_table_names():
            added.append("friend_invites (tabel)")
        if "sync_status" not in insp.get_table_names():
            added.append("sync_status (tabel)")
        if "geo_cache" not in insp.get_table_names():
            added.append("geo_cache (tabel)")
        if "reports" not in insp.get_table_names():
            added.append("reports (tabel)")
        if "enrich_proposals" not in insp.get_table_names():
            added.append("enrich_proposals (tabel)")
        if "photos" not in insp.get_table_names():
            added.append("photos (tabel)")
        for tabel in ("operators", "operator_claims", "edit_proposals", "partner_payments", "feeds",
                      "daguitstappen", "daguitstap_items", "feestjes",
                      "feestje_aanvragen", "ravot_punten", "horeca_kandidaten", "beloningen", "inwisselingen"):
            if tabel not in insp.get_table_names():
                added.append(f"{tabel} (tabel)")
        # nieuwe kolommen op bestaande operator/betaal-tabellen
        if "operators" in insp.get_table_names():
            op_cols = {c["name"] for c in insp.get_columns("operators")}
            for kol, ddl in (
                ("bedrijfsnaam", "ALTER TABLE operators ADD COLUMN bedrijfsnaam VARCHAR(160)"),
                ("btw_nummer", "ALTER TABLE operators ADD COLUMN btw_nummer VARCHAR(20)"),
                ("straat", "ALTER TABLE operators ADD COLUMN straat VARCHAR(160)"),
                ("postcode", "ALTER TABLE operators ADD COLUMN postcode VARCHAR(8)"),
                ("gemeente", "ALTER TABLE operators ADD COLUMN gemeente VARCHAR(80)"),
            ):
                if kol not in op_cols:
                    db.session.execute(text(ddl))
                    added.append(f"operators.{kol}")
        if "partner_payments" in insp.get_table_names():
            pp_cols = {c["name"] for c in insp.get_columns("partner_payments")}
            for kol, ddl in (
                ("odoo_invoice_id", "ALTER TABLE partner_payments ADD COLUMN odoo_invoice_id INTEGER"),
                ("odoo_invoice_ref", "ALTER TABLE partner_payments ADD COLUMN odoo_invoice_ref VARCHAR(40)"),
            ):
                if kol not in pp_cols:
                    db.session.execute(text(ddl))
                    added.append(f"partner_payments.{kol}")
        # Standaard mail- en contentteksten inladen (alleen als ze nog niet bestaan)
        from .seed_content import seed_standaard_content
        n_content = seed_standaard_content()
        if n_content:
            added.append(f"{n_content} standaardteksten")
        # Beloningsvoorwaarden: bestaande (mogelijk zelf bewerkte) voorwaarden-
        # pagina's krijgen de ravotpunten-paragraaf erbij als die nog ontbreekt.
        # We overschrijven bewust niets — enkel aanvullen, vóór "### Wijzigingen"
        # als dat kopje bestaat, anders achteraan.
        from .models import ContentPage
        vw = ContentPage.query.filter_by(slug="voorwaarden").first()
        if vw and vw.inhoud_md and "Ravotpunten en beloningen" not in vw.inhoud_md:
            from .content_teksten import BELONING_VOORWAARDEN
            if "### Wijzigingen" in vw.inhoud_md:
                vw.inhoud_md = vw.inhoud_md.replace(
                    "### Wijzigingen", BELONING_VOORWAARDEN + "\n### Wijzigingen", 1)
            else:
                vw.inhoud_md = vw.inhoud_md.rstrip() + "\n\n" + BELONING_VOORWAARDEN
            added.append("voorwaarden: paragraaf ravotpunten & beloningen")
        # Eenmalige backfill: bestaande OSM-speeltuinen krijgen subtype='playground'
        # (de OSM-adapter zette is_free enkel voor speeltuinen -> betrouwbare proxy),
        # zodat de "verberg speeltuinen"-filter meteen werkt zonder re-sync.
        if "subtype" in cols or "subtype" in [a.split(".")[-1] for a in added]:
            res = db.session.execute(text(
                "UPDATE events SET subtype='playground' "
                "WHERE source='osm' AND is_permanent AND is_free AND subtype IS NULL"))
            if res.rowcount:
                added.append(f"{res.rowcount}× subtype=playground")
        db.session.commit()
        click.echo("Migratie klaar. Toegevoegd: " + (", ".join(added) or "niets nodig"))

    @app.cli.command("sync-uit")
    def sync_uit():
        """Nachtelijke UiT-sync (cron: elke nacht om 03:00)."""
        from .services.uit_sync import run_sync
        n = run_sync()
        click.echo(f"Sync klaar: {n} events verwerkt.")

    @app.cli.command("sync-osm")
    def sync_osm():
        """Wekelijkse OSM-sync (cron: zondagnacht). Zwaar via Overpass, maar
        speeltuinen/parken veranderen traag — één keer per week volstaat."""
        from .services.sources import sync_one
        r = sync_one("osm", force=True)
        click.echo(f"OSM: {r.get('verwerkt', 0)} verwerkt, "
                   f"{r.get('verworpen', 0)} verworpen.")

    @app.cli.command("overture-maandelijks")
    def overture_maandelijks():
        """Maandelijkse Overture-run (cron: 1e van de maand): nieuwe voorraad
        laden, dan de volledige nog-niet-beoordeelde voorraad triëren.
        AI-'ja' gaat automatisch live; de rest wacht op curatie in de
        werkvoorraad."""
        from .services.sources import overture as ov
        from .models import get_setting
        click.echo("1/2 Overture-voorraad verversen…")
        ov.laad_horeca(log=print)
        if (get_setting("verrijk_backend") or "ollama").lower() != "cloud":
            click.echo("⚠️  Backend staat op ollama — triage overgeslagen. "
                       "Zet 'cloud' bij Instellingen en draai 'flask "
                       "beoordeel-voorraad'.")
            return
        click.echo("2/3 Volledige voorraad beoordelen…")
        ov.beoordeel_voorraad(log=print)
        click.echo("3/3 Contactgegevens verrijken…")
        ov.verrijk_contact(log=print)

    @app.cli.command("vul-contact")
    def vul_contact_cmd():
        """Werk enkel website/telefoon/e-mail van bestaande kandidaten bij uit
        Overture (voor voorraad die vóór het e-mailveld werd geladen). Raakt
        doel/AI-advies niet aan; voegt geen nieuwe kandidaten toe."""
        from .services.sources import overture as ov
        n = ov.vul_contact_aan(log=print)
        click.echo(f"Klaar: {n} kandidaten aangevuld.")

    @app.cli.command("herbereken-labels")
    def herbereken_labels_cmd():
        """Herbereken het Ravot-label (brons/zilver/goud) voor alle fiches op
        basis van voorzieningen + reviews. Draai jaarlijks en na veel nieuwe
        reviews."""
        from .services.label import herbereken_labels
        herbereken_labels(log=print)

    @app.cli.command("backup-gelukt")
    @click.argument("grootte_mb", default="0")
    def backup_gelukt_cmd(grootte_mb):
        """Registreer een geslaagde database-backup (aangeroepen door
        scripts/backup-db.sh), zodat de status-check op het dashboard de
        backup-versheid kent zonder de host-backupmap te hoeven zien."""
        from .models import db, MailLog
        try:
            mb = int(grootte_mb)
        except (TypeError, ValueError):
            mb = 0
        db.session.add(MailLog(soort="backup", aantal=mb, ok=True,
                               detail=f"database-backup gelukt ({mb} MB)"))
        db.session.commit()
        click.echo(f"Backup geregistreerd ({mb} MB).")

    @app.cli.command("detecteer-conflicten")
    def detecteer_conflicten_cmd():
        """Vind zaken waar meerdere gezinnen een aangevinkte voorziening
        betwisten -> melding in admin-to-do + hulpmail naar de uitbater.
        Draai bv. wekelijks."""
        from .services.label import detecteer_voorziening_conflicten
        detecteer_voorziening_conflicten(log=print)

    @app.cli.command("herindeel-voorraad")
    def herindeel_voorraad_cmd():
        """Pas de gezin/feest-splitsing toe op de reeds geladen voorraad
        (zonder opnieuw downloaden). Draai dit eenmalig na de update die
        feestprospecten introduceerde."""
        from .services.sources import overture as ov
        g, f = ov.herindeel_voorraad(log=print)
        click.echo(f"Klaar: {g} gezin, {f} feest.")

    @app.cli.command("kuis-kandidaten")
    def kuis_kandidaten_cmd():
        """Ruim ingeladen horeca-kandidaten op die niet door de strengere
        regels raken (bakker/slager/geen adres/lage confidence). Fiches die al
        gecureerd zijn blijven staan. Draai dit eenmalig na een update van de
        filterregels; nieuwe Overture-runs passen de regels al bij het laden toe."""
        from .services.sources import overture as ov
        n = ov.kuis_kandidaten(log=print)
        click.echo(f"Klaar: {n} kandidaten opgekuist.")

    @app.cli.command("verrijk-contact")
    def verrijk_contact_cmd():
        """Vul lege website/telefoon op bestaande fiches aan met Overture-data
        (gematcht op naam + nabijheid). Nul curatiewerk, draai na een
        Overture-run."""
        from .services.sources import overture as ov
        ov.verrijk_contact(log=print)

    @app.cli.command("beoordeel-voorraad")
    def beoordeel_voorraad_cmd():
        """Beoordeel de VOLLEDIGE nog-niet-beoordeelde Overture-voorraad in
        één keer (heel Vlaanderen, zonder per gemeente te zoeken). AI-'ja'
        gaat automatisch live. Dé bespaarknop voor de initiële curatie."""
        from .services.sources import overture as ov
        ov.beoordeel_voorraad(log=print)

    @app.cli.command("sync-all")
    def sync_all_cmd():
        """Alle INGESCHAKELDE bronnen syncen (UiT + Ticketmaster + Toerisme Vl. + OSM)."""
        from .services.sources import sync_all
        for r in sync_all():
            fout = f" — FOUT: {r['fout']}" if r.get("fout") else ""
            click.echo(f"  {r['bron']}: {r['verwerkt']} verwerkt, "
                       f"{r['verworpen']} verworpen (niet kindvriendelijk){fout}")
        click.echo("Sync-all klaar.")

    @app.cli.command("laad-overture")
    def laad_overture_cmd():
        """Download horeca-kandidaten uit Overture Maps (maandelijks/ad hoc).
        Duurt 10-30 min; daarna zoekt de Horeca-verkenner lokaal en snel."""
        from .services.sources import overture
        overture.laad_horeca(log=print)

    @app.cli.command("horeca-ai-alles")
    @click.option("--importeer", is_flag=True,
                  help="Importeer AI-'ja' meteen als gecureerde fiche.")
    @click.option("--limiet", default=0,
                  help="Max. aantal kandidaten deze run (0 = alles).")
    def horeca_ai_alles(importeer, limiet):
        """AI-triage over de VOLLEDIGE kandidatenvoorraad, met optionele
        auto-import van alle 'ja'-oordelen. Dé bespaarknop voor de initiële
        curatie: machines doen de bulk, meldingen van gezinnen doen de rest.

        Kosten-indicatie met de cloud-backend (Haiku): ~1000 kandidaten per
        ~40 batches, samen enkele centen. De hele voorraad van ~48k blijft
        onder een handvol euro. Herstartbaar: al beoordeelde kandidaten
        worden overgeslagen."""
        from .models import Event, HorecaKandidaat, get_setting
        from .services.sources import overture as ov
        al_fiche = db.session.query(Event.ext_id).filter(
            Event.source == "overture", Event.ext_id.isnot(None))
        if (get_setting("verrijk_backend") or "ollama").lower() != "cloud":
            print("⚠️  Backend staat op ollama — dat duurt dagen. Zet bij "
                  "Instellingen → AI-verrijking de backend op 'cloud'.")
        q = HorecaKandidaat.query.filter(
            HorecaKandidaat.ai_advies.is_(None),
            db.or_(HorecaKandidaat.gesloten.is_(False),
                   HorecaKandidaat.gesloten.is_(None)),
            ~HorecaKandidaat.ext_id.in_(al_fiche))
        te_doen = q.limit(limiet).all() if limiet else q.all()
        print(f"Te beoordelen: {len(te_doen)} kandidaten…")
        stap = 500
        totaal_beoordeeld = 0
        for i in range(0, len(te_doen), stap):
            blok = te_doen[i:i + stap]
            n = ov.ai_triage(blok)
            totaal_beoordeeld += n
            print(f"  {min(i + stap, len(te_doen))}/{len(te_doen)} "
                  f"({totaal_beoordeeld} beoordeeld)")
            if n == 0:
                print("  ⚠️  Backend antwoordt niet meer — gestopt. Herstart "
                      "dit commando gerust; het pikt op waar het bleef.")
                break
        if importeer:
            jas = HorecaKandidaat.query.filter_by(ai_advies="ja") \
                .filter(~HorecaKandidaat.ext_id.in_(al_fiche),
                        db.or_(HorecaKandidaat.gesloten.is_(False),
                               HorecaKandidaat.gesloten.is_(None))).all()
            print(f"Auto-import van {len(jas)} AI-'ja'-zaken…")
            n = ov.importeer([(k.ext_id,
                               "zomerbar" if k.zomerbar_hint else
                               ("winterbar" if k.winterbar_hint else "horeca"))
                              for k in jas])
            # Machine-gecureerd = geen werkvoorraad: meldingen en reviews van
            # gezinnen zijn vanaf nu de onderhoudsploeg.
            from .models import Event
            Event.query.filter(Event.source == "overture",
                               Event.nagekeken.is_(False)) \
                .update({"nagekeken": True}, synchronize_session=False)
            db.session.commit()
            print(f"Geïmporteerd: {n} fiches (gecureerd, geen nazicht nodig).")
        print("Klaar.")

    @app.cli.command("sync-bron")
    @click.argument("naam")
    @click.option("--force", is_flag=True, help="Sync ook als de koppeling uit staat (testen).")
    def sync_bron(naam, force):
        """Eén bron syncen. NAAM = uit | tm | tv | osm."""
        from .services.sources import sync_one, REGISTRY
        if naam not in REGISTRY:
            click.echo(f"Onbekende bron '{naam}'. Kies uit: {', '.join(REGISTRY)}.")
            return
        r = sync_one(naam, force=force)
        fout = f" — FOUT: {r['fout']}" if r.get("fout") else ""
        click.echo(f"{r['bron']}: {r['verwerkt']} verwerkt, "
                   f"{r.get('verworpen', 0)} verworpen{fout}")

    @app.cli.command("kuis-testdata")
    @click.option("--bevestig", is_flag=True, help="Écht wissen (zonder deze vlag: enkel tonen).")
    def kuis_testdata_cmd(bevestig):
        """Verwijder UiT-testdata en houd enkel wat er echt in mag.

        Wist: events uit de bron 'uit' die NIET goedgekeurd zijn (curated) en
        NIET door een gebruiker zijn toegevoegd. Behoudt: alle door jou als
        Ravot-waardig gemarkeerde fiches, alle gebruikersbijdragen, en alle
        plekken uit OSM/Wikidata/andere bronnen. Ruimt daarna verweesde
        organisatoren/locaties/reeksen op. Raakt de databank-volume nooit aan.
        """
        from .models import Event, Organizer, Venue, EditionSeries
        from sqlalchemy import delete, select

        doel = Event.query.filter(Event.source == "uit",
                                  Event.curated.is_(False),
                                  Event.submitted_by.is_(None))
        te_wissen = doel.count()
        totaal_uit = Event.query.filter(Event.source == "uit").count()
        behoud_cur = Event.query.filter(Event.source == "uit", Event.curated.is_(True)).count()
        behoud_usr = Event.query.filter(Event.source == "uit",
                                        Event.submitted_by.isnot(None)).count()
        anders = Event.query.filter(Event.source != "uit").count()

        click.echo("── Opkuis UiT-testdata ─────────────────────────")
        click.echo(f"  UiT-events totaal        : {totaal_uit}")
        click.echo(f"  → te wissen (testdata)   : {te_wissen}")
        click.echo(f"  → behouden (Ravot-waardig): {behoud_cur}")
        click.echo(f"  → behouden (gebruiker)   : {behoud_usr}")
        click.echo(f"  Andere bronnen (blijven) : {anders}")

        if not bevestig:
            click.echo("\nDROOGLOOP — er is niets gewist. Draai met --bevestig om echt te wissen.")
            click.echo("Maak eerst een back-up: docker compose exec db pg_dump -U ravot ravot > backup.sql")
            return

        ids = [r[0] for r in doel.with_entities(Event.id).all()]
        gewist = 0
        for i in range(0, len(ids), 1000):
            brok = ids[i:i + 1000]
            # eerst alle child-rijen die naar deze events verwijzen
            for tbl in db.metadata.sorted_tables:
                if tbl.name != "events" and "event_id" in tbl.c:
                    db.session.execute(delete(tbl).where(tbl.c.event_id.in_(brok)))
            db.session.execute(delete(Event.__table__).where(Event.id.in_(brok)))
            db.session.commit()
            gewist += len(brok)

        # Verweesde reeksen/organisatoren/locaties opruimen — in de juiste
        # volgorde (reeksen eerst, want die verwijzen naar organisatoren/locaties)
        # en enkel wat door géén event én géén overblijvende reeks gebruikt wordt.
        wees = 0
        # 1) reeksen zonder events
        ser_gebruikt = select(Event.series_id).where(Event.series_id.isnot(None))
        q = EditionSeries.query.filter(~EditionSeries.id.in_(ser_gebruikt))
        wees += q.count()
        q.delete(synchronize_session=False)
        db.session.commit()
        # 2) organisatoren/locaties die nergens meer aan hangen (event noch reeks)
        for Model, ev_kol, ser_kol in (
                (Organizer, Event.organizer_id, EditionSeries.organizer_id),
                (Venue, Event.venue_id, EditionSeries.venue_id)):
            uit_events = select(ev_kol).where(ev_kol.isnot(None))
            uit_reeksen = select(ser_kol).where(ser_kol.isnot(None))
            q = Model.query.filter(~Model.id.in_(uit_events), ~Model.id.in_(uit_reeksen))
            wees += q.count()
            q.delete(synchronize_session=False)
        db.session.commit()

        click.echo(f"\n✓ {gewist} testevents gewist, {wees} verweesde organisatoren/locaties/reeksen opgeruimd.")
        click.echo("Goedgekeurde, gebruikers- en OSM/Wikidata-fiches bleven behouden.")

    @app.cli.command("send-weekendmail")
    def send_weekendmail():
        """Donderdagmail (cron: donderdag 17:00)."""
        from .models import get_bool, db, MailLog
        if not get_bool("weekendmail_aan"):
            click.echo("Weekendmail staat uit in de instellingen.")
            return
        from .services.magic import send_mail
        from .services.weekendmail import send_all
        try:
            n = send_all(send_mail)
            db.session.add(MailLog(soort="weekendmail", aantal=n, ok=True,
                                   detail=f"verstuurd naar {n} gezinnen"))
            db.session.commit()
            click.echo(f"Weekendmail verstuurd naar {n} gezinnen.")
        except Exception as e:
            db.session.add(MailLog(soort="weekendmail", aantal=0, ok=False,
                                   detail=str(e)[:280]))
            db.session.commit()
            raise

    @app.cli.command("send-maandagmail")
    def send_maandagmail():
        """Maandagvraag: scores binnenhalen (cron: maandag 10:00)."""
        from .models import get_bool, db, MailLog
        if not get_bool("maandagmail_aan"):
            click.echo("Maandagmail staat uit in de instellingen.")
            return
        from .services.magic import send_mail
        from .services.maandagmail import send_all
        try:
            n = send_all(send_mail)
            db.session.add(MailLog(soort="maandagmail", aantal=n, ok=True,
                                   detail=f"verstuurd naar {n} gezinnen"))
            db.session.commit()
            click.echo(f"Maandagmail verstuurd naar {n} gezinnen.")
        except Exception as e:
            db.session.add(MailLog(soort="maandagmail", aantal=0, ok=False,
                                   detail=str(e)[:280]))
            db.session.commit()
            raise

    @app.cli.command("verrijk-batch")
    @click.option("--n", default=20, help="Aantal plekken om te verrijken.")
    def verrijk_batch_cmd(n):
        """Genereer AI-verrijkingsvoorstellen voor plekken (naar de wachtrij)."""
        from .enrich import verrijk_batch
        gelukt, mislukt = verrijk_batch(limit=n)
        click.echo(f"Verrijking klaar: {gelukt} voorstellen, {mislukt} mislukt.")

    @app.cli.command("herbereken-kwaliteit")
    def herbereken_kwaliteit_cmd():
        """Kwaliteitsscore (0-100) van alle fiches herberekenen."""
        from .kwaliteit import herbereken_alles
        n = herbereken_alles()
        click.echo(f"Kwaliteit herberekend: {n} fiches bijgewerkt.")

    @app.cli.command("kuis-beschrijvingen")
    def kuis_beschrijvingen_cmd():
        """Verwijder achtergebleven HTML-opmaak uit bestaande beschrijvingen."""
        from .models import Event
        from .services.uit_sync import _strip_html
        ids = [r[0] for r in db.session.query(Event.id).all()]
        n = 0
        for i in range(0, len(ids), 500):
            for ev in Event.query.filter(Event.id.in_(ids[i:i + 500])).all():
                if ev.description and ("<" in ev.description or "&" in ev.description):
                    schoon = _strip_html(ev.description)
                    if schoon != ev.description:
                        ev.description = schoon
                        n += 1
            db.session.commit()
        click.echo(f"Beschrijvingen opgekuist: {n} fiches bijgewerkt.")

    @app.cli.command("dedup")
    def dedup_cmd():
        """Dubbele permanente POI's (dezelfde plek uit meerdere bronnen) verbergen."""
        from .services.sources import dedup_pois
        n = dedup_pois()
        click.echo(f"Dedup klaar: {n} dubbels verborgen.")

    @app.cli.command("purge-bron")
    @click.argument("naam")
    @click.option("--ja", is_flag=True, help="Bevestig verwijderen zonder vraag.")
    def purge_bron(naam, ja):
        """Verwijder ALLE events van één bron (bv. de UiT-testdata).
        NAAM = uit | tm | tv | osm. Ruimt ook verweesde locaties/reeksen op."""
        from .models import Event
        from .services.sources import purge_source
        n = Event.query.filter_by(source=naam).count()
        if n == 0:
            click.echo(f"Geen events met bron '{naam}'. Niets te doen.")
            return
        if not ja:
            click.confirm(f"{n} events van bron '{naam}' definitief verwijderen?",
                          abort=True)
        verwijderd = purge_source(naam)
        click.echo(f"Bron '{naam}' opgeruimd: {verwijderd} events + verweesde "
                   "locaties/reeksen verwijderd.")

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.password_option()
    def create_admin(email, password):
        """Admin aanmaken. 2FA stel je daarna in via QR bij de eerste login."""
        import pyotp
        from argon2 import PasswordHasher
        from .models import Admin
        secret = pyotp.random_base32()
        db.session.add(Admin(email=email.lower(), totp_secret=secret,
                             pw_hash=PasswordHasher().hash(password),
                             totp_confirmed=False))
        db.session.commit()
        click.echo(f"Admin '{email}' aangemaakt. ✅\n"
                   "Ga naar /beheer, log in met e-mail + wachtwoord, "
                   "en scan dan de QR-code die verschijnt om 2FA in te stellen.")

    @app.cli.command("reset-admin-2fa")
    @click.argument("email")
    def reset_admin_2fa(email):
        """2FA opnieuw instellen: nieuwe secret, QR-flow bij volgende login.
        Handig als je je authenticator-app of telefoon kwijt bent."""
        import pyotp
        from .models import Admin
        admin = Admin.query.filter_by(email=email.lower()).first()
        if not admin:
            click.echo(f"Geen admin met e-mail '{email}'.")
            return
        admin.totp_secret = pyotp.random_base32()
        admin.totp_confirmed = False
        db.session.commit()
        click.echo(f"2FA gereset voor '{email}'. ✅\n"
                   "Log opnieuw in op /beheer; je krijgt een nieuwe QR-code te scannen.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Demo-events rond enkele gemeenten (dev zonder UiT-key)."""
        import random
        from datetime import datetime, timedelta
        from .models import Event, Organizer, Venue
        from .services.uit_sync import find_or_create_series, slugify, update_centroids

        plaatsen = [
            ("Roeselare", "8800", 50.946, 3.123), ("Gent", "9000", 51.054, 3.722),
            ("Brugge", "8000", 51.209, 3.225), ("Izegem", "8870", 50.914, 3.213),
            ("Kortrijk", "8500", 50.828, 3.265),
        ]
        titels = [
            ("Kinderboerderij openhouderdag", ["natuur", "buiten"], 2, 10, 0, False),
            ("Knutselatelier herfst", ["creatief"], 4, 9, 5, True),
            ("Kleuterzwemfestijn", ["sport"], 3, 6, 4, True),
            ("Buitenspeeldag", ["buiten", "sport"], 3, 12, 0, False),
            ("Poppentheater De Vlieger", ["cultuur"], 3, 8, 8, True),
            ("Natuurwandeling met gids", ["natuur", "buiten"], 5, 12, 0, False),
            ("Bouwdorp", ["creatief", "buiten"], 6, 12, 2, False),
            ("Familievoorstelling circus", ["cultuur"], 4, 12, 12, True),
        ]
        now = datetime.utcnow()
        i = 0
        for gemeente, postcode, lat, lng in plaatsen:
            org = Organizer(uit_id=f"demo-org-{gemeente}", name=f"Jeugddienst {gemeente}",
                            slug=slugify(f"jeugddienst-{gemeente}"))
            venue = Venue(uit_id=f"demo-venue-{gemeente}", name=f"Domein {gemeente}",
                          gemeente=gemeente, postcode=postcode,
                          lat=lat + random.uniform(-0.01, 0.01),
                          lng=lng + random.uniform(-0.01, 0.01))
            db.session.add_all([org, venue])
            db.session.flush()
            for titel, cats, lo, hi, prijs, indoor in titels:
                i += 1
                start = now + timedelta(days=random.randint(0, 6),
                                        hours=random.randint(9, 15))
                series = find_or_create_series(titel, org, venue)
                db.session.add(Event(
                    uit_id=f"demo-{i}", source="uit",
                    slug=f"{slugify(titel)}-{gemeente.lower()}-{i}",
                    title=titel, description=f"{titel} in {gemeente} — een demo-event.",
                    start=start, end=start + timedelta(hours=3),
                    gemeente=gemeente, postcode=postcode,
                    lat=venue.lat, lng=venue.lng,
                    age_min=lo, age_max=hi, categories=cats, indoor=indoor,
                    is_free=(prijs == 0),
                    price_info=[{"name": "basis", "price": prijs},
                                {"name": "kinderen", "price": max(0, prijs - 3),
                                 "min_age": 0, "max_age": 12}] if prijs else
                               [{"name": "basis", "price": 0}],
                    organizer_id=org.id, venue_id=venue.id, series_id=series.id,
                ))
        update_centroids()
        db.session.commit()
        click.echo(f"{i} demo-events aangemaakt in {len(plaatsen)} gemeenten.")
