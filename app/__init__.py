import secrets

import click
from flask import Flask, render_template

from .config import Config
from .extensions import csrf, db, limiter


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from .routes.public import bp as public_bp
    from .routes.auth import bp as auth_bp
    from .routes.account import bp as account_bp
    from .routes.admin import bp as admin_bp
    # let op: public als laatste registreren — die heeft de catch-all /<gemeente>
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)

    @app.context_processor
    def inject_guest():
        from flask import session
        g = session.get("guest") or {}
        from .vakantie import vakantiecontext
        ctx = {"guest": g, "has_guest": bool(g.get("postcode")),
               "vakantie": vakantiecontext(), "bewaard_ids": set(), "geliked_ids": set()}
        # Welke events heeft dit gezin bewaard? (voor de hartjes op de kaarten)
        fid = session.get("family_id")
        if fid:
            from .models import SavedEvent, Interaction
            try:
                ctx["bewaard_ids"] = {s.event_id for s in
                                      SavedEvent.query.filter_by(family_id=fid)}
                ctx["geliked_ids"] = {i.event_id for i in
                    Interaction.query.filter_by(family_id=fid, type="like")}
            except Exception:
                pass
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

    @app.cli.command("migrate-db")
    def migrate_db():
        """Voegt ontbrekende kolommen/tabellen toe zonder data te wissen."""
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        added = []
        cols = {c["name"] for c in insp.get_columns("events")}
        if "has_vlieg" not in cols:
            db.session.execute(text(
                "ALTER TABLE events ADD COLUMN has_vlieg BOOLEAN DEFAULT FALSE"))
            added.append("events.has_vlieg")
        # totp_confirmed op admins: bestaande admins op FALSE → doorlopen QR-flow
        admin_cols = {c["name"] for c in insp.get_columns("admins")}
        if "totp_confirmed" not in admin_cols:
            db.session.execute(text(
                "ALTER TABLE admins ADD COLUMN totp_confirmed BOOLEAN DEFAULT FALSE NOT NULL"))
            added.append("admins.totp_confirmed")
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
        # settings-tabel (nieuw in admin-config) — create_all maakt enkel wat ontbreekt
        db.create_all()  # maakt ook friend_invites-tabel aan
        if "settings" not in insp.get_table_names():
            added.append("settings (tabel)")
        if "friend_invites" not in insp.get_table_names():
            added.append("friend_invites (tabel)")
        # Standaard mail- en contentteksten inladen (alleen als ze nog niet bestaan)
        from .seed_content import seed_standaard_content
        n_content = seed_standaard_content()
        if n_content:
            added.append(f"{n_content} standaardteksten")
        db.session.commit()
        click.echo("Migratie klaar. Toegevoegd: " + (", ".join(added) or "niets nodig"))

    @app.cli.command("sync-uit")
    def sync_uit():
        """Nachtelijke UiT-sync (cron: elke nacht om 03:00)."""
        from .services.uit_sync import run_sync
        n = run_sync()
        click.echo(f"Sync klaar: {n} events verwerkt.")

    @app.cli.command("send-weekendmail")
    def send_weekendmail():
        """Donderdagmail (cron: donderdag 17:00)."""
        from .models import get_bool
        if not get_bool("weekendmail_aan"):
            click.echo("Weekendmail staat uit in de instellingen.")
            return
        from .services.magic import send_mail
        from .services.weekendmail import send_all
        n = send_all(send_mail)
        click.echo(f"Weekendmail verstuurd naar {n} gezinnen.")

    @app.cli.command("send-maandagmail")
    def send_maandagmail():
        """Maandagvraag: scores binnenhalen (cron: maandag 10:00)."""
        from .models import get_bool
        if not get_bool("maandagmail_aan"):
            click.echo("Maandagmail staat uit in de instellingen.")
            return
        from .services.magic import send_mail
        from .services.maandagmail import send_all
        n = send_all(send_mail)
        click.echo(f"Maandagmail verstuurd naar {n} gezinnen.")

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
