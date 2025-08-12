from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from io import TextIOWrapper
import csv
import datetime
import os
import pytz
from datetime import datetime


# SQLAlchemy aggregation helper
from sqlalchemy import func

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app




def get_serializer():
    # uses your existing SECRET_KEY
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])





app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)


from datetime import timedelta

app.config.update(
    REMEMBER_COOKIE_DURATION=timedelta(days=30),  # stay logged in for 30 days
    REMEMBER_COOKIE_SECURE=True,                  # only over HTTPS
    REMEMBER_COOKIE_HTTPONLY=True,                # JS can’t read cookie
    REMEMBER_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)




import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///test_local.db")
# Render sometimes hands out old-style URLs
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


db = SQLAlchemy()
db.init_app(app)

# Models
class Athlete(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name  = db.Column(db.String(100), nullable=False)
    grade      = db.Column(db.Integer, nullable=True)     # was False
    gender     = db.Column(db.String(50), nullable=True)  # was False
    team_id    = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    athletes = db.relationship('Athlete', backref='team', lazy=True)

from sqlalchemy.schema import UniqueConstraint

class Attendance(db.Model):
    __table_args__ = (UniqueConstraint('athlete_id', 'date', name='uq_attendance_day'),)
    id = db.Column(db.Integer, primary_key=True)
    athlete_id = db.Column(db.Integer, db.ForeignKey('athlete.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # ISO format string is fine
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.String(255))
    athlete = db.relationship("Athlete", backref="attendance_records")


class Coach(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=True)



def rename_teams_to_coaches():
    """Rename the first four teams (by ID) to Brad, Chad, Grace, Klatt.
       If fewer than 4 teams exist, create them."""
    desired = ["Brad", "Chad", "Grace", "Klatt"]

    teams = Team.query.order_by(Team.id).all()

    # Create up to 4 if missing
    while len(teams) < 4:
        t = Team(name=f"Team{len(teams)+1}")
        db.session.add(t)
        db.session.flush()
        teams.append(t)

    # Rename first four
    for i, name in enumerate(desired):
        teams[i].name = name

    db.session.commit()
    print("✅ Team names set to:", ", ".join(desired))



from sqlalchemy import inspect, text

def ensure_athlete_columns():
    insp = inspect(db.engine)
    try:
        cols = {c['name'] for c in insp.get_columns('athlete')}
    except Exception as e:
        print("Could not inspect 'athlete' table:", e)
        return

    stmts = []
    if 'grade' not in cols:
        stmts.append("ALTER TABLE athlete ADD COLUMN IF NOT EXISTS grade INTEGER")
    if 'gender' not in cols:
        stmts.append("ALTER TABLE athlete ADD COLUMN IF NOT EXISTS gender VARCHAR(20)")

    if not stmts:
        print("Athlete table already has needed columns.")
        return

    with db.engine.begin() as conn:
        for s in stmts:
            print("Running:", s)
            conn.execute(text(s))
    print("Athlete columns ensured.")

from sqlalchemy import text

def ensure_attendance_unique_index():
    # Enforce one row per (athlete_id, date). Safe to run multiple times.
    stmt = text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_attendance_athlete_date
        ON attendance (athlete_id, date)
    """)
    with db.engine.begin() as conn:
        conn.execute(stmt)
    print("Attendance unique index ensured.")

from sqlalchemy import text

def ensure_athlete_unique_index(per_team=True):
    """
    Create a functional UNIQUE index to prevent duplicate names.
    - per_team=True => unique on (lower(first), lower(last), team_id)
    - per_team=False => unique on (lower(first), lower(last))
    """
    with db.engine.begin() as conn:
        if per_team:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_athlete_name_team
                ON athlete (lower(first_name), lower(last_name), team_id);
            """))
        else:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_athlete_name_global
                ON athlete (lower(first_name), lower(last_name));
            """))




# Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(Coach, int(user_id))
    except (TypeError, ValueError):
        return None


# Routes
@app.route("/")
def home():
    athletes = Athlete.query.order_by(Athlete.last_name).all()
    return render_template("index.html", athletes=athletes)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = Coach.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            remember = bool(request.form.get("remember"))
            login_user(user, remember=remember)
            return redirect(url_for("attendance"))
        else:
            error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

from zoneinfo import ZoneInfo  # make sure this is imported once


@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    central = ZoneInfo("America/Chicago")
    today = datetime.datetime.now(central).date().isoformat()


    # Get team_id from querystring or form; normalize to int or None
    raw_team_id = request.args.get("team_id") or request.form.get("team_id")
    try:
        selected_team_id = int(raw_team_id) if raw_team_id else None
    except (TypeError, ValueError):
        selected_team_id = None

    # Handle toggle + note update
    if request.method == "POST":
        athlete_id = request.form.get("athlete_id")
        note = (request.form.get("note") or "").strip()
        if athlete_id:
            try:
                aid = int(athlete_id)
            except (TypeError, ValueError):
                aid = None

            if aid:
                record = Attendance.query.filter_by(athlete_id=aid, date=today).first()
                if record:
                    record.status = "Absent" if record.status == "Present" else "Present"
                    record.notes = note
                else:
                    db.session.add(Attendance(
                        athlete_id=aid,
                        date=today,
                        status="Present",
                        notes=note
                    ))
                db.session.commit()

        # Keep current team filter after submit
        return redirect(url_for("attendance", team_id=selected_team_id))

    # ===== Auto-create Present rows on GET so green = saved in DB =====
    q = Athlete.query
    if selected_team_id:
        q = q.filter_by(team_id=selected_team_id)
    for athlete in q.all():
        exists = Attendance.query.filter_by(athlete_id=athlete.id, date=today).first()
        if not exists:
            db.session.add(Attendance(
                athlete_id=athlete.id,
                date=today,
                status="Present",
                notes=None
            ))
    db.session.commit()
    # =================================================================

    # GET: fetch athletes (filtered if team selected)
    if selected_team_id:
        athletes = (Athlete.query
                    .filter_by(team_id=selected_team_id)
                    .order_by(Athlete.last_name, Athlete.first_name)
                    .all())
    else:
        athletes = (Athlete.query
                    .order_by(Athlete.last_name, Athlete.first_name)
                    .all())

    # Attendance & notes for today (TEAM-FILTER AWARE)
    # Only pull records for the selected team (or all teams if no filter)
    today_records = (
        Attendance.query
        .join(Athlete, Attendance.athlete_id == Athlete.id)
        .filter(
            Attendance.date == today,
            (Athlete.team_id == selected_team_id) if selected_team_id else True
        )
        .all()
    )
    attendance_data = {r.athlete_id: r.status for r in today_records}
    notes_data = {r.athlete_id: r.notes for r in today_records}

    present_count = sum(1 for s in attendance_data.values() if s == "Present")
    absent_count = sum(1 for s in attendance_data.values() if s == "Absent")
    # Unmarked = athletes shown minus number of records for those athletes
    unmarked_count = max(0, len(athletes) - len(attendance_data))

    teams = Team.query.order_by(Team.name).all()

    return render_template(
        "attendance.html",
        athletes=athletes,
        attendance=attendance_data,
        notes=notes_data,
        teams=teams,
        selected_team_id=selected_team_id,
        date=today,
        present_count=present_count,
        absent_count=absent_count,
        unmarked_count=unmarked_count
    )



@app.route("/history", methods=["GET", "POST"])
@login_required
def history():
    # All known dates (or today if none yet)
    all_dates = [d[0] for d in db.session.query(Attendance.date)
                 .distinct().order_by(Attendance.date.desc()).all()] or [datetime.datetime.today().isoformat()]

    # Pull inputs from POST (form) or GET (link)
    selected_date = (request.form.get("selected_date")
                     or request.args.get("selected_date")
                     or all_dates[0])

    raw_team = request.form.get("team_id") or request.args.get("team_id")
    try:
        selected_team_id = int(raw_team) if raw_team else None
    except (TypeError, ValueError):
        selected_team_id = None

    # Default to coach's team if nothing chosen
    if selected_team_id is None and getattr(current_user, "team_id", None):
        selected_team_id = current_user.team_id

    # Build query: everyone for the day, with left join to attendance
    query = (
        db.session.query(
            Athlete.first_name,        # [0]
            Athlete.last_name,         # [1]
            Attendance.status,         # [2]
            Attendance.notes           # [3]
        )
        .outerjoin(
            Attendance,
            (Attendance.athlete_id == Athlete.id) & (Attendance.date == selected_date)
        )
    )
    if selected_team_id:
        query = query.filter(Athlete.team_id == selected_team_id)

    history_data = query.order_by(Athlete.last_name, Athlete.first_name).all()

    # Counts for Present / Absent / Unmarked
    present_count = sum(1 for _, _, s, _ in history_data if s == "Present")
    absent_count  = sum(1 for _, _, s, _ in history_data if s == "Absent")
    unmarked_count = sum(1 for _, _, s, _ in history_data if s not in ("Present", "Absent"))

    # IMPORTANT: pass teams as (id, name) tuples to match team[0]/team[1] in your template
    teams = db.session.query(Team.id, Team.name).order_by(Team.name).all()

    return render_template(
        "history.html",
        dates=all_dates,
        selected_date=selected_date,
        teams=teams,
        selected_team_id=selected_team_id,
        history_data=history_data,
        present_count=present_count,
        absent_count=absent_count,
        unmarked_count=unmarked_count,
    )

@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        user = Coach.query.filter_by(username=username).first()

        # Always respond the same (don’t leak which usernames exist)
        if not user:
            flash("If that user exists, we sent a reset link.", "success")
            return redirect(url_for("login"))

        s = get_serializer()
        token = s.dumps({"uid": user.id})
        reset_url = url_for("reset_password_token", token=token, _external=True)

        # Try email if you wired it; otherwise log to console/Render logs
        try:
            from app import send_email_via_sendgrid  # if you added the helper earlier
            ok, err = send_email_via_sendgrid(
                getattr(user, "email", None),
                "HP XC Password Reset",
                f"Click to reset your password: <a href='{reset_url}'>{reset_url}</a>"
            )
        except Exception:
            ok, err = (False, "missing_helper")

        if ok:
            flash("We emailed you a reset link.", "success")
        else:
            print("🔐 Password reset link (fallback):", reset_url)
            flash("Reset link created. Email not configured—link logged in server.", "success")

        return redirect(url_for("login"))

    return render_template("forgot.html")


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password_token(token):
    s = get_serializer()
    try:
        data = s.loads(token, max_age=3600)  # 1 hour validity
    except SignatureExpired:
        flash("Reset link expired. Please try again.", "error")
        return redirect(url_for("forgot"))
    except BadSignature:
        flash("Invalid reset link.", "error")
        return redirect(url_for("forgot"))

    user = Coach.query.get(data.get("uid"))
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("forgot"))

    if request.method == "POST":
        new_pw = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm") or "").strip()
        if len(new_pw) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(request.url)
        if new_pw != confirm:
            flash("Passwords do not match.", "error")
            return redirect(request.url)

        user.password = generate_password_hash(new_pw)
        db.session.commit()
        flash("Password updated. You can log in now.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password_token.html")



@app.route("/add_coach", methods=["GET", "POST"])
@login_required
def add_coach():
    teams = Team.query.order_by(Team.name).all()
    message = None
    if request.method == "POST":
        name = request.form["name"]
        username = request.form["username"]
        password = request.form["password"]
        team_id = request.form.get("team_id") or None
        hashed_password = generate_password_hash(password)
        try:
            new_coach = Coach(name=name, username=username, password=hashed_password, team_id=int(team_id) if team_id else None)
            db.session.add(new_coach)
            db.session.commit()
            message = "Coach added successfully."
        except Exception as e:
            db.session.rollback()
            message = f"Error: {str(e)}"
    return render_template("add_coach.html", teams=teams, message=message)

import csv
from io import TextIOWrapper

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

@app.route("/import_csv", methods=["GET", "POST"])
@login_required
def import_csv():
    if request.method == "POST":
        try:
            file = request.files.get("file")
            if not file or not getattr(file, "filename", ""):
                flash("No file uploaded.", "error")
                return redirect(url_for("import_csv"))

            f = TextIOWrapper(file.stream, encoding="utf-8", newline="")
            reader = csv.DictReader(f)

            # Team lookups
            all_teams = Team.query.all()
            teams_by_name = { (t.name or "").strip(): t for t in all_teams }
            teams_by_id   = { int(t.id): t for t in all_teams }

            added = 0
            skipped_missing_names = 0
            skipped_unknown_team  = 0
            skipped_infile_dupes  = 0
            skipped_db_dupes      = 0

            # in-file duplicate guard: per-team uniqueness (first+last+team)
            seen = set()  # keys like ("ava","jones", team_id or None)

            for row in reader:
                fn = (row.get("first_name") or "").strip()
                ln = (row.get("last_name")  or "").strip()
                if not fn or not ln:
                    skipped_missing_names += 1
                    continue

                # Resolve team
                team_val = (row.get("team_name") or row.get("team_id") or "").strip()
                team = None
                if team_val:
                    if team_val.isdigit():
                        team = teams_by_id.get(int(team_val))
                    else:
                        team = teams_by_name.get(team_val)
                team_id = team.id if team else None
                if team_val and not team:
                    skipped_unknown_team += 1
                    # still import with team_id=None? choose to proceed without team:
                    # comment next two lines if you want to *skip* entirely instead.
                    # continue  # uncomment to skip rows with unknown team
                    # (fallthrough keeps team_id=None)

                # Optional fields
                grade_raw = (row.get("grade") or "").strip()
                try:
                    grade = int(grade_raw) if grade_raw != "" else None
                except ValueError:
                    grade = None
                gender = (row.get("gender") or "").strip() or None

                key = (fn.lower(), ln.lower(), team_id)
                if key in seen:
                    skipped_infile_dupes += 1
                    continue
                seen.add(key)

                # DB-level duplicate check (case-insensitive, per team)
                exists = (
                    db.session.query(Athlete.id)
                    .filter(
                        func.lower(Athlete.first_name) == fn.lower(),
                        func.lower(Athlete.last_name)  == ln.lower(),
                        Athlete.team_id == team_id
                    )
                    .first()
                )
                if exists:
                    skipped_db_dupes += 1
                    continue

                # Add and flush so we can catch a unique-index violation early
                try:
                    db.session.add(Athlete(
                        first_name=fn,
                        last_name=ln,
                        grade=grade,
                        gender=gender,
                        team_id=team_id
                    ))
                    db.session.flush()
                    added += 1
                except IntegrityError:
                    db.session.rollback()
                    skipped_db_dupes += 1
                    # keep going

            db.session.commit()

            bits = [f"Imported {added} athletes."]
            if skipped_missing_names:
                bits.append(f"Skipped {skipped_missing_names} missing name(s).")
            if skipped_unknown_team:
                bits.append(f"{skipped_unknown_team} row(s) had unknown team.")
            if skipped_infile_dupes:
                bits.append(f"Skipped {skipped_infile_dupes} duplicate row(s) in file.")
            if skipped_db_dupes:
                bits.append(f"Skipped {skipped_db_dupes} already on roster.")
            flash(" ".join(bits), "success")
            return redirect(url_for("attendance"))

        except Exception as e:
            db.session.rollback()
            print("❌ CSV import failed:", e)
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("import_csv"))

    return render_template("import_csv.html")


import csv, io, zipfile, datetime
from flask import Response, abort
from functools import wraps

# If you don't already have this:
def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if getattr(current_user, "username", "") != "admin":
            abort(403)
        return f(*a, **kw)
    return w

def _csv_response(rows, headers, filename_base):
    si = io.StringIO()
    w = csv.writer(si)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    out = si.getvalue()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    resp = Response(out, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename_base}-{ts}.csv"
    return resp

@app.route("/admin/export", methods=["GET"])
@login_required
@admin_required
def export_data():
    """
    Export CSV of a selected table or a ZIP of all:
      /admin/export?table=attendance&team_id=1&since=2025-08-01&until=2025-08-31
      /admin/export?table=all
    tables: teams | athletes | attendance | coaches | all
    """
    table = (request.args.get("table") or "all").lower()
    team_id = request.args.get("team_id", type=int)
    since = (request.args.get("since") or "").strip()  # YYYY-MM-DD
    until = (request.args.get("until") or "").strip()

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    # ----- TEAMS -----
    if table == "teams":
        rows = db.session.query(Team.id, Team.name).order_by(Team.id).all()
        return _csv_response(rows, ["id", "name"], "teams")

    # ----- ATHLETES -----
    if table == "athletes":
        q = (db.session.query(
                Athlete.id,
                Athlete.first_name,
                Athlete.last_name,
                Athlete.grade,
                Athlete.gender,
                Athlete.team_id,
                Team.name.label("team_name"),
            )
            .join(Team, Team.id == Athlete.team_id, isouter=True)
            .order_by(Athlete.last_name, Athlete.first_name))
        if team_id:
            q = q.filter(Athlete.team_id == team_id)
        rows = q.all()
        return _csv_response(
            rows,
            ["id","first_name","last_name","grade","gender","team_id","team_name"],
            "athletes"
        )

    # ----- ATTENDANCE -----
    if table == "attendance":
        q = (db.session.query(
                Attendance.id,
                Attendance.athlete_id,
                Athlete.first_name,
                Athlete.last_name,
                Athlete.team_id,
                Team.name.label("team_name"),
                Attendance.date,
                Attendance.status,
                Attendance.notes,
            )
            .join(Athlete, Athlete.id == Attendance.athlete_id)
            .join(Team, Team.id == Athlete.team_id, isouter=True)
        )
        if team_id:
            q = q.filter(Athlete.team_id == team_id)
        if since:
            q = q.filter(Attendance.date >= since)
        if until:
            q = q.filter(Attendance.date <= until)
        q = q.order_by(Attendance.date.desc(), Athlete.last_name, Athlete.first_name)
        rows = q.all()
        return _csv_response(
            rows,
            ["id","athlete_id","first_name","last_name","team_id","team_name","date","status","notes"],
            "attendance"
        )

    # ----- COACHES (no password hashes) -----
    if table == "coaches":
        q = (db.session.query(
                Coach.id, Coach.name, Coach.username, Coach.email, Coach.team_id, Team.name.label("team_name")
            )
            .join(Team, Team.id == Coach.team_id, isouter=True)
            .order_by(Coach.name))
        if team_id:
            q = q.filter(Coach.team_id == team_id)
        rows = q.all()
        return _csv_response(
            rows,
            ["id","name","username","email","team_id","team_name"],
            "coaches"
        )

    # ----- ALL: build a ZIP with 4 CSVs -----
    if table == "all":
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # teams
            teams = db.session.query(Team.id, Team.name).order_by(Team.id).all()
            _add_csv_to_zip(zf, "teams", ["id","name"], teams, ts)

            # athletes
            aq = (db.session.query(
                    Athlete.id, Athlete.first_name, Athlete.last_name,
                    Athlete.grade, Athlete.gender, Athlete.team_id,
                    Team.name.label("team_name"))
                  .join(Team, Team.id == Athlete.team_id, isouter=True)
                  .order_by(Athlete.last_name, Athlete.first_name))
            if team_id:
                aq = aq.filter(Athlete.team_id == team_id)
            _add_csv_to_zip(zf, "athletes",
                ["id","first_name","last_name","grade","gender","team_id","team_name"],
                aq.all(), ts)

            # attendance
            atq = (db.session.query(
                    Attendance.id, Attendance.athlete_id,
                    Athlete.first_name, Athlete.last_name,
                    Athlete.team_id, Team.name.label("team_name"),
                    Attendance.date, Attendance.status, Attendance.notes)
                   .join(Athlete, Athlete.id == Attendance.athlete_id)
                   .join(Team, Team.id == Athlete.team_id, isouter=True))
            if team_id:
                atq = atq.filter(Athlete.team_id == team_id)
            if since:
                atq = atq.filter(Attendance.date >= since)
            if until:
                atq = atq.filter(Attendance.date <= until)
            atq = atq.order_by(Attendance.date.desc(), Athlete.last_name, Athlete.first_name)
            _add_csv_to_zip(zf, "attendance",
                ["id","athlete_id","first_name","last_name","team_id","team_name","date","status","notes"],
                atq.all(), ts)

            # coaches
            cq = (db.session.query(
                    Coach.id, Coach.name, Coach.username, Coach.email, Coach.team_id, Team.name.label("team_name"))
                  .join(Team, Team.id == Coach.team_id, isouter=True)
                  .order_by(Coach.name))
            if team_id:
                cq = cq.filter(Coach.team_id == team_id)
            _add_csv_to_zip(zf, "coaches",
                ["id","name","username","email","team_id","team_name"],
                cq.all(), ts)

        mem.seek(0)
        resp = Response(mem.read(), mimetype="application/zip")
        resp.headers["Content-Disposition"] = f"attachment; filename=export-{ts}.zip"
        return resp

    abort(400)

def _add_csv_to_zip(zf, base, headers, rows, ts):
    si = io.StringIO()
    w = csv.writer(si)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    zf.writestr(f"{base}-{ts}.csv", si.getvalue())



from datetime import date
from sqlalchemy import func, and_

@app.route("/flagged_athletes", methods=["GET", "POST"])
@login_required
def flagged_athletes():
    # ---- Inputs ----
    # threshold: minimum absences to flag (default 5)
    try:
        min_abs = int(request.values.get("min_absences", 5))
    except (TypeError, ValueError):
        min_abs = 5

    # date filters (optional). You’re using string dates (ISO) in Attendance.date, so keep them as strings.
    since = (request.values.get("since") or "").strip()  # "YYYY-MM-DD" or ""
    until = (request.values.get("until") or "").strip()

    # team filter: admin can pick; coaches default to their team
    raw_team_id = request.values.get("team_id")
    selected_team_id = None
    try:
        selected_team_id = int(raw_team_id) if raw_team_id else None
    except (TypeError, ValueError):
        selected_team_id = None

    # If user has a team and no team explicitly chosen, default to it
    if selected_team_id is None and getattr(current_user, "team_id", None):
        selected_team_id = current_user.team_id

    # ---- Query ----
    q = (
        db.session.query(
            Attendance.athlete_id.label("athlete_id"),
            func.count(Attendance.id).label("absence_count"),
        )
        .join(Athlete, Athlete.id == Attendance.athlete_id)
        .filter(Attendance.status == "Absent")
        .group_by(Attendance.athlete_id)
        .having(func.count(Attendance.id) >= min_abs)
    )

    # Apply optional filters
    if selected_team_id:
        q = q.filter(Athlete.team_id == selected_team_id)
    if since:
        q = q.filter(Attendance.date >= since)
    if until:
        q = q.filter(Attendance.date <= until)

    sub = q.subquery()

    # Join to get names + team
    flagged = (
        db.session.query(
            Athlete.id,
            Athlete.first_name,
            Athlete.last_name,
            Team.name.label("team_name"),
            sub.c.absence_count,
        )
        .join(sub, Athlete.id == sub.c.athlete_id)
        .join(Team, Team.id == Athlete.team_id, isouter=True)
        .order_by(sub.c.absence_count.desc(), Athlete.last_name, Athlete.first_name)
        .all()
    )

    # Teams list for dropdown (admin sees all; coaches see theirs)
    if getattr(current_user, "username", "") == "admin":
        teams = Team.query.order_by(Team.name).all()
    else:
        teams = Team.query.filter(Team.id == current_user.team_id).all()

    return render_template(
        "flagged.html",
        flagged=flagged,
        teams=teams,
        selected_team_id=selected_team_id,
        min_absences=min_abs,
        since=since,
        until=until,
    )

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

@app.route("/manage_roster", methods=["GET", "POST"])
@login_required
def manage_roster():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            first_name = (request.form.get("first_name") or "").strip()
            last_name  = (request.form.get("last_name")  or "").strip()
            grade      = request.form.get("grade")
            gender     = request.form.get("gender")
            team_id    = request.form.get("team_id") or None

            if not first_name or not last_name:
                flash("First and last name are required.", "error")
                return redirect(url_for("manage_roster"))

            team_id_int = int(team_id) if team_id else None
            grade_int = int(grade) if grade else None

            # case-insensitive, per-team dedupe
            exists = (
                db.session.query(Athlete.id)
                .filter(
                    func.lower(Athlete.first_name) == first_name.lower(),
                    func.lower(Athlete.last_name)  == last_name.lower(),
                    Athlete.team_id == team_id_int
                )
                .first()
            )
            if exists:
                flash(f"Duplicate blocked: {first_name} {last_name} already on this team.", "error")
                return redirect(url_for("manage_roster"))

            try:
                db.session.add(Athlete(
                    first_name=first_name,
                    last_name=last_name,
                    grade=grade_int,
                    gender=gender,
                    team_id=team_id_int
                ))
                db.session.commit()
                flash(f"Added athlete {first_name} {last_name}.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Duplicate detected by database constraint.", "error")
            except Exception as e:
                db.session.rollback()
                flash(f"Error adding athlete: {e}", "error")

        elif action == "edit":
            athlete_id = request.form.get("athlete_id")
            if not athlete_id:
                flash("Missing athlete_id.", "error")
                return redirect(url_for("manage_roster"))

            first_name = (request.form.get("first_name") or "").strip()
            last_name  = (request.form.get("last_name")  or "").strip()
            grade      = request.form.get("grade")
            gender     = request.form.get("gender")
            team_id    = request.form.get("team_id") or None

            if not first_name or not last_name:
                flash("First and last name are required.", "error")
                return redirect(url_for("manage_roster"))

            try:
                athlete = Athlete.query.get(int(athlete_id))
                if not athlete:
                    flash("Athlete not found.", "error")
                    return redirect(url_for("manage_roster"))

                team_id_int  = int(team_id) if team_id else None
                grade_int    = int(grade) if grade else None

                # dedupe check, excluding self
                exists = (
                    db.session.query(Athlete.id)
                    .filter(
                        func.lower(Athlete.first_name) == first_name.lower(),
                        func.lower(Athlete.last_name)  == last_name.lower(),
                        Athlete.team_id == team_id_int,
                        Athlete.id != athlete.id
                    )
                    .first()
                )
                if exists:
                    flash(f"Edit blocked: {first_name} {last_name} already on this team.", "error")
                    return redirect(url_for("manage_roster"))

                athlete.first_name = first_name
                athlete.last_name  = last_name
                athlete.grade      = grade_int
                athlete.gender     = gender
                athlete.team_id    = team_id_int

                db.session.commit()
                flash("Athlete updated.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Duplicate detected by database constraint.", "error")
            except Exception as e:
                db.session.rollback()
                flash(f"Error updating athlete: {e}", "error")

        elif action == "delete":
            athlete_id = request.form.get("athlete_id")
            if athlete_id:
                try:
                    Attendance.query.filter_by(athlete_id=athlete_id).delete()
                    Athlete.query.filter_by(id=athlete_id).delete()
                    db.session.commit()
                    flash("Athlete removed.", "success")
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error removing athlete: {e}", "error")

        return redirect(url_for("manage_roster"))

    # GET
    athletes = (
        db.session.query(
            Athlete.id,        # 0
            Athlete.first_name,# 1
            Athlete.last_name, # 2
            Athlete.grade,     # 3
            Athlete.gender,    # 4
            Athlete.team_id,   # 5
            Team.name.label("team_name"), # 6
        )
        .join(Team, Team.id == Athlete.team_id, isouter=True)
        .order_by(Athlete.last_name, Athlete.first_name)
        .all()
    )
    teams = db.session.query(Team.id, Team.name).order_by(Team.name).all()
    return render_template("manage_roster.html", athletes=athletes, teams=teams)


# Manage athlete absences: view and delete absences for a selected athlete
@app.route("/manage_absences", methods=["GET", "POST"])
@login_required
def manage_absences():
    # Show ALL athletes to all coaches (no team gating)
    athletes = (
        db.session.query(Athlete.id, Athlete.first_name, Athlete.last_name)
        .order_by(Athlete.last_name, Athlete.first_name)
        .all()
    )

    selected_id = request.form.get("athlete_id") or request.args.get("athlete_id")
    delete_id = request.form.get("delete_id")

    # Optional add-absence inputs
    add_date = (request.form.get("add_date") or "").strip()
    add_note = (request.form.get("add_note") or "").strip()
    action = request.form.get("action")

    # Normalize selected athlete id
    try:
        sid = int(selected_id) if selected_id else None
    except ValueError:
        sid = None

    # Handle deletion (only delete Absent rows)
   # Handle deletion -> actually mark Present instead of deleting
    if delete_id:
        try:
            rec = Attendance.query.get(delete_id)
            if rec and rec.status == "Absent":
                rec.status = "Present"
                rec.notes = None  # optional: clear note when marking present
                db.session.commit()
            else:
                # fallback: if something's weird, just ignore gracefully
                db.session.rollback()
        except Exception:
            db.session.rollback()


    # Handle add (mark a date as Absent, upserting if a Present exists)
    if action == "add_absence" and sid and add_date:
        try:
            rec = Attendance.query.filter_by(athlete_id=sid, date=add_date).first()
            if rec:
                rec.status = "Absent"
                if add_note:
                    rec.notes = add_note
            else:
                db.session.add(Attendance(
                    athlete_id=sid,
                    date=add_date,
                    status="Absent",
                    notes=add_note or None
                ))
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Load existing absences for selected athlete
    absences = []
    if sid:
        absences = (
            db.session.query(Attendance.id, Attendance.date, Attendance.notes)
            .filter(Attendance.athlete_id == sid, Attendance.status == "Absent")
            .order_by(Attendance.date.desc())
            .all()
        )

    return render_template(
        "manage_absences.html",
        athletes=athletes,
        selected_id=sid,
        absences=absences,
    )



from sqlalchemy import and_

@app.route("/athlete_report", methods=["GET", "POST"])
@login_required
def athlete_report():
    # Pull selection from either POST (dropdown auto-submit) or GET (links)
    selected_id = (request.form.get("athlete_id") or request.args.get("athlete_id") or "").strip() or None
    try:
        selected_id = int(selected_id) if selected_id else None
    except (TypeError, ValueError):
        selected_id = None

    # Optional date range (ISO strings, matches your Attendance.date type)
    since = (request.values.get("since") or "").strip()
    until = (request.values.get("until") or "").strip()

    # Limit athletes list to coach's team unless admin
    if getattr(current_user, "username", "") == "admin":
        athletes_q = db.session.query(Athlete.id, Athlete.first_name, Athlete.last_name)
    else:
        athletes_q = db.session.query(Athlete.id, Athlete.first_name, Athlete.last_name)\
                               .filter(Athlete.team_id == current_user.team_id)

    athletes = athletes_q.order_by(Athlete.last_name, Athlete.first_name).all()

    # Build absences list for selected athlete
    absences = []
    if selected_id:
        q = db.session.query(Attendance.date, Attendance.notes)\
            .filter(
                Attendance.athlete_id == selected_id,
                Attendance.status == "Absent"
            )
        if since:
            q = q.filter(Attendance.date >= since)
        if until:
            q = q.filter(Attendance.date <= until)
        absences = q.order_by(Attendance.date.desc()).all()

    return render_template(
        "athlete_report.html",
        athletes=athletes,
        selected_id=selected_id,
        absences=absences,
        since=since,
        until=until,
    )


# Reset coach passwords
@app.route("/reset_password", methods=["GET", "POST"])
@login_required
def reset_password():
    """
    Allow an administrator to reset a coach's password.  All coaches are
    displayed in a dropdown; selecting one and entering a new password will
    update the stored password hash.  Feedback is provided after a
    successful reset.  Users without admin privileges can still access
    this route but will only see the interface and cannot determine
    another coach's credentials because the password is never displayed.
    """
    message = None
    if request.method == "POST":
        coach_id = request.form.get("coach_id")
        new_password = request.form.get("new_password")
        if coach_id and new_password:
            coach = Coach.query.get(coach_id)
            if coach:
                coach.password = generate_password_hash(new_password)
                db.session.commit()
                message = "Password reset successfully."
    # Load list of coaches for the dropdown
    coaches = (
        db.session.query(Coach.id, Coach.name)
        .order_by(Coach.name)
        .all()
    )
    return render_template("reset_password.html", coaches=coaches, message=message)







def seed_default_coach():
    from werkzeug.security import generate_password_hash
    if not Coach.query.first():
        coach = Coach(
            name="Admin",
            username="admin",
            password=generate_password_hash("adminpass")
        )
        db.session.add(coach)
        db.session.commit()
        print("✅ Default coach created: admin / adminpass")


def seed_teams():
    if not Team.query.first():
        for name in ["Undercut", "Chicane", "Box Box", "Push Mode"]:
            db.session.add(Team(name=name))
        db.session.commit()
        print("✅ Teams seeded!")



if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_athlete_columns()
        ensure_attendance_unique_index()
        ensure_athlete_unique_index(per_team=True)   # <-- keep this
        print("✅ Tables created")
        seed_default_coach()
        seed_teams()
        if os.getenv("RENAME_TEAMS_ON_BOOT") == "1":
            rename_teams_to_coaches()


    app.run(debug=True)
else:
    with app.app_context():
        try:
            db.create_all()
            ensure_athlete_columns()
            ensure_attendance_unique_index()
            ensure_athlete_unique_index(per_team=True)   # <-- add this here too
            print("✅ Tables created")
            seed_default_coach()
            seed_teams()
            if os.getenv("RENAME_TEAMS_ON_BOOT") == "1":
                rename_teams_to_coaches()


        except Exception as e:
            print(f"❌ Error during db.create_all(): {e}")




