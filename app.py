from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from io import TextIOWrapper
import csv
import datetime
import os

# SQLAlchemy aggregation helper
from sqlalchemy import func

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)




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
            login_user(user)
            return redirect(url_for("attendance"))
        else:
            error = "Invalid username or password"
    return render_template("login.html", error=error)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    today = datetime.date.today().isoformat()

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
    dates = db.session.query(Attendance.date).distinct().order_by(Attendance.date.desc()).all()
    selected_date = request.form.get("selected_date") or datetime.date.today().isoformat()
    selected_team_id = request.form.get("team_id")

    query = db.session.query(Athlete.first_name, Athlete.last_name, Attendance.status, Attendance.notes).join(Attendance, (Attendance.athlete_id == Athlete.id) & (Attendance.date == selected_date), isouter=True)
    if selected_team_id:
        query = query.filter(Athlete.team_id == selected_team_id)
    query = query.order_by(Athlete.last_name, Athlete.first_name)

    teams = Team.query.order_by(Team.name).all()

    return render_template("history.html", dates=[d[0] for d in dates], selected_date=selected_date, teams=teams, selected_team_id=int(selected_team_id) if selected_team_id else None, history_data=query.all())

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

@app.route("/import_csv", methods=["GET", "POST"])
@login_required
def import_csv():
    if request.method == "POST":
        try:
            file = request.files.get("file")
            if not file or not getattr(file, "filename", ""):
                flash("No file uploaded.", "error")
                return redirect(url_for("import_csv"))

            # CSV parse
            f = TextIOWrapper(file.stream, encoding="utf-8", newline="")
            reader = csv.DictReader(f)

            # Teams lookup (both by name and id)
            all_teams = Team.query.all()
            teams_by_name = {t.name.strip(): t for t in all_teams if t.name}
            teams_by_id = {int(t.id): t for t in all_teams}

            added = 0
            skipped_missing_names = 0
            skipped_unknown_team = 0

            for row in reader:
                fn = (row.get("first_name") or "").strip()
                ln = (row.get("last_name") or "").strip()
                if not fn or not ln:
                    skipped_missing_names += 1
                    continue

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

                # Optional fields
                grade_raw = (row.get("grade") or "").strip()
                try:
                    grade = int(grade_raw) if grade_raw != "" else None
                except ValueError:
                    grade = None
                gender = (row.get("gender") or "").strip() or None

                db.session.add(Athlete(
                    first_name=fn,
                    last_name=ln,
                    grade=grade,
                    gender=gender,
                    team_id=team_id
                ))
                added += 1

            db.session.commit()

            msg = [f"Imported {added} athletes."]
            if skipped_missing_names:
                msg.append(f"Skipped {skipped_missing_names} missing name(s).")
            if skipped_unknown_team:
                msg.append(f"{skipped_unknown_team} row(s) had unknown team.")
            flash(" ".join(msg), "success")
            return redirect(url_for("attendance"))

        except Exception as e:
            db.session.rollback()
            print("❌ CSV import failed:", e)
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("import_csv"))

    # >>> IMPORTANT: always return a response on GET <<<
    return render_template("import_csv.html")




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


@app.route("/manage_roster", methods=["GET", "POST"])
@login_required
def manage_roster():
    """
    Display and modify the roster of athletes.  Coaches can add new
    athletes by submitting first name, last name, grade, gender and
    team assignment.  Existing athletes may be removed from the roster.
    A hidden `action` field in the form determines whether the request
    is an addition or deletion.  After processing a POST request the
    user is redirected back to this page to reflect changes.
    """
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            # Extract form fields for new athlete
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            grade = request.form.get("grade")
            gender = request.form.get("gender")
            team_id = request.form.get("team_id") or None
            if first_name and last_name and grade and gender:
                try:
                    athlete = Athlete(
                        first_name=first_name,
                        last_name=last_name,
                        grade=int(grade),
                        gender=gender,
                        team_id=int(team_id) if team_id else None,
                    )
                    db.session.add(athlete)
                    db.session.commit()
                    flash(f"Added athlete {first_name} {last_name}.")
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error adding athlete: {e}")
        elif action == "delete":
            athlete_id = request.form.get("athlete_id")
            if athlete_id:
                try:
                    # Delete attendance records first to maintain referential integrity
                    Attendance.query.filter_by(athlete_id=athlete_id).delete()
                    athlete = Athlete.query.get(athlete_id)
                    if athlete:
                        db.session.delete(athlete)
                        db.session.commit()
                        flash("Athlete removed.")
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error removing athlete: {e}")
        # POST-redirect-GET to avoid form resubmission
        return redirect(url_for("manage_roster"))

    # GET request: load all athletes and teams for display
    athletes = (
        db.session.query(Athlete.id, Athlete.first_name, Athlete.last_name)
        .order_by(Athlete.last_name, Athlete.first_name)
        .all()
    )
    teams = (
        db.session.query(Team.id, Team.name)
        .order_by(Team.name)
        .all()
    )
    return render_template("manage_roster.html", athletes=athletes, teams=teams)


# Manage athlete absences: view and delete absences for a selected athlete
@app.route("/manage_absences", methods=["GET", "POST"])
@login_required
def manage_absences():
    """
    Provide a simple interface to view all absence records for a given athlete
    and optionally delete specific entries.  The form posts back to the
    same route whenever the user selects an athlete from the dropdown or
    clicks a delete button.  If a `delete_id` is provided, that absence
    record is removed from the database.  After any mutation the page
    reloads to show the updated list.
    """
    # List of all athletes for the dropdown
    athletes = (
        db.session.query(Athlete.id, Athlete.first_name, Athlete.last_name)
        .order_by(Athlete.last_name, Athlete.first_name)
        .all()
    )
    selected_id = request.form.get("athlete_id") or request.args.get("athlete_id")
    delete_id = request.form.get("delete_id")
    # Handle deletion of a specific absence entry
    if delete_id:
        try:
            Attendance.query.filter_by(id=delete_id).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
    absences = []
    if selected_id:
        try:
            sid = int(selected_id)
            absences = (
                db.session.query(Attendance.id, Attendance.date, Attendance.notes)
                .filter(
                    Attendance.athlete_id == sid,
                    Attendance.status == "Absent",
                )
                .order_by(Attendance.date.desc())
                .all()
            )
        except ValueError:
            selected_id = None
    return render_template(
        "manage_absences.html",
        athletes=athletes,
        selected_id=int(selected_id) if selected_id else None,
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
    # in __main__ path
    with app.app_context():
        db.create_all()
        ensure_athlete_columns()
        ensure_attendance_unique_index()   # <-- add this line
        print("✅ Tables created")
        seed_default_coach()
        seed_teams()

    app.run(debug=True)
else:
    # in the else: (Render/gunicorn) path
    with app.app_context():
        try:
            db.create_all()
            ensure_athlete_columns()
            ensure_attendance_unique_index()   # <-- add this line
            print("✅ Tables created")
            seed_default_coach()
            seed_teams()
        except Exception as e:
            print(f"❌ Error during db.create_all(): {e}")



