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
app.secret_key = 'boomer'

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///test_local.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy()
db.init_app(app)

# Models
class Athlete(db.Model):
    """
    Represents an individual athlete.  We store the athlete's name along with
    their grade and gender so that rosters can be managed directly from within
    the application.  Each athlete belongs to a team via a foreign key.  The
    underlying SQLite database already contains `grade` and `gender` columns,
    so we map them here as part of the ORM model.  If the columns are
    missing from an existing deployment the application will still function
    because SQLAlchemy will ignore unbound fields when reading from the DB.
    """
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    # Grade level (e.g. 9 for freshman).  Required in the roster form.
    grade = db.Column(db.Integer, nullable=False)
    # Gender of the athlete.  Required in the roster form.
    gender = db.Column(db.String(50), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    athletes = db.relationship('Athlete', backref='team', lazy=True)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    athlete_id = db.Column(db.Integer, db.ForeignKey('athlete.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.String(255), nullable=True)
    athlete = db.relationship("Athlete", backref="attendance_records")

class Coach(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)

# Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return Coach.query.get(int(user_id))

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

    # Get team_id from GET or POST
    selected_team_id = request.args.get("team_id") or request.form.get("team_id")
    print(f"🔍 team_id from request: '{selected_team_id}'")

    # Normalize team_id to int or None
    try:
        selected_team_id = int(selected_team_id) if selected_team_id else None
    except (ValueError, TypeError):
        selected_team_id = None
    print(f"✅ Normalized selected_team_id: {selected_team_id}")

    # POST: Handle attendance marking
    if request.method == "POST":
        athlete_id = request.form.get("athlete_id")
        note = request.form.get("note", "")
        if athlete_id:
            record = Attendance.query.filter_by(athlete_id=athlete_id, date=today).first()
            if record:
                record.status = "Absent" if record.status == "Present" else "Present"
                record.notes = note
            else:
                db.session.add(Attendance(
                    athlete_id=athlete_id,
                    date=today,
                    status="Present",
                    notes=note
                ))
            db.session.commit()
        # Keep selected team after submission
        return redirect(url_for("attendance", team_id=selected_team_id))

    # GET: Load athletes
    if selected_team_id:
        athletes = Athlete.query.filter_by(team_id=selected_team_id).order_by(Athlete.last_name).all()
    else:
        athletes = Athlete.query.order_by(Athlete.last_name).all()

    # Attendance data
    attendance_records = Attendance.query.filter_by(date=today).all()
    attendance_data = {r.athlete_id: r.status for r in attendance_records}
    notes_data = {r.athlete_id: r.notes for r in attendance_records}
    present_count = sum(1 for s in attendance_data.values() if s == "Present")
    absent_count = sum(1 for s in attendance_data.values() if s == "Absent")
    unmarked_count = len(athletes) - (present_count + absent_count)
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

@app.route('/import_csv', methods=['GET', 'POST'])
@login_required
def import_csv():
    if request.method == 'POST':
        file = request.files['file']
        if file and file.filename.endswith('.csv'):
            try:
                stream = TextIOWrapper(file.stream)
                csv_input = csv.reader(stream)
                next(csv_input)
                for row in csv_input:
                    first_name, last_name, team_id = row
                    db.session.add(Athlete(first_name=first_name, last_name=last_name, team_id=int(team_id)))
                db.session.commit()
                flash('CSV imported successfully.')
            except Exception as e:
                flash(f'Error importing CSV: {e}')
            return redirect(url_for('home'))
        flash('Invalid file type. Please upload a .csv file.')
    return render_template('import_csv.html')

@app.route("/flagged_athletes")
@login_required
def flagged_athletes():
    """
    Display athletes who have accumulated five or more absences.

    We aggregate attendance records by athlete and count the number of
    "Absent" entries per athlete across all dates.  Only athletes with
    five or more absences are returned.  The resulting list contains
    tuples of (athlete_id, first_name, last_name, absence_count).
    """
    # Build a subquery counting absences per athlete
    sub = (
        db.session.query(
            Attendance.athlete_id,
            func.count(Attendance.id).label("absence_count")
        )
        .filter(Attendance.status == "Absent")
        .group_by(Attendance.athlete_id)
        .having(func.count(Attendance.id) >= 5)
        .subquery()
    )
    # Join with the Athlete table to get names
    flagged = (
        db.session.query(
            Athlete.id,
            Athlete.first_name,
            Athlete.last_name,
            sub.c.absence_count,
        )
        .join(sub, Athlete.id == sub.c.athlete_id)
        .order_by(sub.c.absence_count.desc())
        .all()
    )
    return render_template("flagged.html", flagged=flagged)

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


# Individual athlete absence report (read-only)
@app.route("/athlete_report", methods=["GET", "POST"])
@login_required
def athlete_report():
    """
    Show a historical list of absence dates and notes for a single athlete.
    Coaches can select an athlete from the dropdown; upon selection the form
    automatically submits and the page refreshes with that athlete's
    absences displayed.  Unlike manage_absences, this route does not allow
    deleting entries.
    """
    athletes = (
        db.session.query(Athlete.id, Athlete.first_name, Athlete.last_name)
        .order_by(Athlete.last_name, Athlete.first_name)
        .all()
    )
    selected_id = request.form.get("athlete_id") or request.args.get("athlete_id")
    absences = []
    if selected_id:
        try:
            sid = int(selected_id)
            absences = (
                db.session.query(Attendance.date, Attendance.notes)
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
        "athlete_report.html",
        athletes=athletes,
        selected_id=int(selected_id) if selected_id else None,
        absences=absences,
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


@app.route("/seed_teams")
def seed_teams():
    if not Team.query.first():
        for name in ["Undercut", "Chicane", "Box Box", "Push Mode"]:
            db.session.add(Team(name=name))
        db.session.commit()
        return "✅ Teams seeded!"
    return "⚠️ Teams already exist."

@app.route("/nuke")
def nuke():
    db.drop_all()
    db.create_all()
    return "💣 Database nuked and recreated."


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
        print("✅ Tables created")
        seed_default_coach()
        seed_teams()
    app.run(debug=True)
else:
    with app.app_context():
        try:
            db.create_all()
            print("✅ Tables created")
            seed_default_coach()
            seed_teams()
        except Exception as e:
            print(f"❌ Error during db.create_all(): {e}")


            

