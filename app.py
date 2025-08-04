import sqlite3, datetime
from flask_sqlalchemy import SQLAlchemy
import os

from flask import Flask, render_template, g, request, redirect, url_for
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import check_password_hash
from flask import request, redirect, url_for, flash
import csv
from io import TextIOWrapper

app = Flask(__name__)
app.secret_key = 'boomer'  # Needed for session management

# Connect to the PostgreSQL database on Render
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Create a SQLAlchemy instance
db = SQLAlchemy(app)

class Athlete(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    team_id = db.Column(db.Integer, nullable=False)



DATABASE = 'attendance.db'

# Setup LoginManager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Coach model for authentication
class Coach(UserMixin):
    def __init__(self, id, name, username, password, team_id):
        self.id = id
        self.name = name
        self.username = username
        self.password = password
        self.team_id = team_id

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, name, username, password, team_id FROM coaches WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        return Coach(*row)
    return None

# Home route
@app.route("/")
def home():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT first_name, last_name FROM athletes ORDER BY last_name")
    athletes = cursor.fetchall()
    return render_template("index.html", athletes=athletes)

# Login route
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id, name, username, password, team_id FROM coaches WHERE username = ?", (username,))
        row = cursor.fetchone()

        if row and check_password_hash(row[3], password):
            user = Coach(*row)
            login_user(user)
            return redirect(url_for("attendance"))
        else:
            error = "Invalid username or password"

    return render_template("login.html", error=error)

# Logout route
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# Attendance route
@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    db = get_db()
    cursor = db.cursor()
    today = datetime.date.today().isoformat()

    # Handle attendance marking or toggling
    if request.method == "POST":
        athlete_id = request.form.get("athlete_id")
        note = request.form.get("note", "")

        if athlete_id:
            # Check for existing record (should only be "Absent")
            cursor.execute("SELECT id FROM attendance WHERE athlete_id = ? AND date = ?", (athlete_id, today))
            existing = cursor.fetchone()

            if existing:
    # If already marked absent, change to present
                cursor.execute(
                    "UPDATE attendance SET status = ?, notes = ? WHERE id = ?",
                    ("Present", note, existing[0])
                )
            else:
                # Insert new absence
                cursor.execute(
                    "INSERT INTO attendance (athlete_id, date, status, notes) VALUES (?, ?, ?, ?)",
                    (athlete_id, today, "Absent", note)
                )

            db.commit()
        return redirect(url_for('attendance', team_id=request.args.get("team_id")))


    # Handle team filtering and attendance display
    cursor.execute("SELECT id, name FROM teams ORDER BY name")
    teams = cursor.fetchall()

    team_id = request.args.get("team_id")
    if team_id:
        cursor.execute(
            "SELECT id, first_name, last_name FROM athletes WHERE team_id = ? ORDER BY last_name",
            (team_id,)
        )
    else:
        cursor.execute("SELECT id, first_name, last_name FROM athletes ORDER BY last_name")
    athletes = cursor.fetchall()

    cursor.execute("SELECT athlete_id, status FROM attendance WHERE date = ?", (today,))
    attendance_data = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT athlete_id, notes FROM attendance WHERE date = ?", (today,))
    notes_data = {row[0]: row[1] for row in cursor.fetchall()}

    # Attendance summary
    present_count = sum(1 for status in attendance_data.values() if status == "Present")
    absent_count = sum(1 for status in attendance_data.values() if status == "Absent")
    unmarked_count = len(athletes) - (present_count + absent_count)

    return render_template(
        "attendance.html",
        athletes=athletes,
        attendance=attendance_data,
        notes=notes_data,
        teams=teams,
        selected_team_id=int(team_id) if team_id else None,
        date=today,
        present_count=present_count,
        absent_count=absent_count,
        unmarked_count=unmarked_count
    )

@app.route("/history", methods=["GET", "POST"])
@login_required
def history():
    db = get_db()
    cursor = db.cursor()

    # Get available dates
    cursor.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    dates = [row[0] for row in cursor.fetchall()]

    # Get available teams
    cursor.execute("SELECT id, name FROM teams ORDER BY name")
    teams = cursor.fetchall()

    # Get selected values
    selected_date = request.form.get("selected_date") or datetime.date.today().isoformat()
    selected_team_id = request.form.get("team_id")

    # Base query
    query = """
        SELECT athletes.first_name, athletes.last_name, 
               COALESCE(attendance.status, 'Present') AS status, 
               attendance.notes
        FROM athletes
        LEFT JOIN attendance 
            ON attendance.athlete_id = athletes.id 
            AND attendance.date = ?
    """

    params = [selected_date]

    if selected_team_id:
        query += " WHERE athletes.team_id = ?"
        params.append(selected_team_id)

    query += " ORDER BY athletes.last_name, athletes.first_name"

    cursor.execute(query, tuple(params))
    history_data = cursor.fetchall()

    return render_template(
        "history.html",
        dates=dates,
        teams=teams,
        selected_date=selected_date,
        selected_team_id=int(selected_team_id) if selected_team_id else None,
        history_data=history_data
    )




@app.route("/history/<date>")
@login_required
def history_by_date(date):
    db = get_db()
    cursor = db.cursor()

    # Get all dates for dropdown
    cursor.execute("SELECT DISTINCT date FROM attendance ORDER BY date DESC")
    dates = [row[0] for row in cursor.fetchall()]

    # Get all athletes and their status for the given date
    cursor.execute("""
        SELECT athletes.first_name, athletes.last_name, 
               COALESCE(attendance.status, 'Present') as status, 
               attendance.notes
        FROM athletes
        LEFT JOIN attendance 
            ON attendance.athlete_id = athletes.id 
            AND attendance.date = ?
        ORDER BY athletes.last_name, athletes.first_name
    """, (date,))
    history_data = cursor.fetchall()

    return render_template(
        "history.html",
        dates=dates,
        selected_date=date,
        history_data=history_data
    )

# Add this to your `app.py`
@app.route("/athlete_report", methods=["GET", "POST"])
@login_required
def athlete_report():
    db = get_db()
    cursor = db.cursor()

    # Get all athletes for the dropdown
    cursor.execute("SELECT id, first_name, last_name FROM athletes ORDER BY last_name, first_name")
    athletes = cursor.fetchall()

    selected_id = int(request.form.get("athlete_id")) if request.form.get("athlete_id") else None
    absences = []

    if selected_id:
        cursor.execute("""
    SELECT date, notes
    FROM attendance
    WHERE athlete_id = ? AND status = 'Absent'
    ORDER BY date DESC
""", (selected_id,))

        absences = cursor.fetchall()

    return render_template(
        "athlete_report.html",
        athletes=athletes,
        selected_id=int(selected_id) if selected_id else None,
        absences=absences
    )

@app.route("/flagged")
@login_required
def flagged_athletes():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT athletes.id, athletes.first_name, athletes.last_name, COUNT(*) AS absence_count
        FROM attendance
        JOIN athletes ON attendance.athlete_id = athletes.id
        WHERE attendance.status = 'Absent'
        GROUP BY athletes.id
        HAVING absence_count >= 5
        ORDER BY absence_count DESC
    """)

    flagged = cursor.fetchall()

    return render_template("flagged.html", flagged=flagged)

from werkzeug.security import generate_password_hash

@app.route("/add_coach", methods=["GET", "POST"])
@login_required
def add_coach():
    db = get_db()
    cursor = db.cursor()

    # Fetch all teams for dropdown
    cursor.execute("SELECT id, name FROM teams ORDER BY name")
    teams = cursor.fetchall()

    message = None

    if request.method == "POST":
        name = request.form["name"]
        username = request.form["username"]
        password = request.form["password"]
        team_id = request.form.get("team_id") or None

        hashed_password = generate_password_hash(password)

        try:
            cursor.execute("""
                INSERT INTO coaches (name, username, password, team_id)
                VALUES (?, ?, ?, ?)
            """, (name, username, hashed_password, team_id))
            db.commit()
            message = "Coach added successfully."
        except sqlite3.IntegrityError:
            message = "Username already exists."

    return render_template("add_coach.html", teams=teams, message=message)


@app.route("/reset_password", methods=["GET", "POST"])
@login_required
def reset_password():
    db = get_db()
    cursor = db.cursor()

    # Get list of coaches
    cursor.execute("SELECT id, name FROM coaches ORDER BY name")
    coaches = cursor.fetchall()

    message = None

    if request.method == "POST":
        coach_id = request.form["coach_id"]
        new_password = request.form["new_password"]
        hashed = generate_password_hash(new_password)

        cursor.execute("UPDATE coaches SET password = ? WHERE id = ?", (hashed, coach_id))
        db.commit()
        message = "Password successfully reset."

    return render_template("reset_password.html", coaches=coaches, message=message)
@app.route("/manage_roster", methods=["GET", "POST"])
@login_required
def manage_roster():
    db = get_db()
    cursor = db.cursor()

    # Get all teams for the dropdown
    cursor.execute("SELECT id, name FROM teams ORDER BY name")
    teams = cursor.fetchall()

    message = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            first_name = request.form["first_name"]
            last_name = request.form["last_name"]
            grade = request.form["grade"]
            gender = request.form["gender"]
            team_id = request.form.get("team_id") or None

            try:
                cursor.execute("""
                    INSERT INTO athletes (first_name, last_name, grade, gender, team_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (first_name, last_name, grade, gender, team_id))
                db.commit()
                message = "Athlete added."
            except sqlite3.IntegrityError as e:
                message = f"Error adding athlete: {e}"

        elif action == "delete":
            athlete_id = request.form["athlete_id"]
            cursor.execute("DELETE FROM athletes WHERE id = ?", (athlete_id,))
            db.commit()
            message = "Athlete removed."

    # Always fetch current athlete list
    cursor.execute("SELECT id, first_name, last_name FROM athletes ORDER BY last_name")
    athletes = cursor.fetchall()

    return render_template("manage_roster.html", teams=teams, athletes=athletes, message=message)


@app.route("/manage_absences", methods=["GET", "POST"])
@login_required
def manage_absences():
    db = get_db()
    cursor = db.cursor()

    # Get all athletes for the dropdown
    cursor.execute("SELECT id, first_name, last_name FROM athletes ORDER BY LOWER(last_name), LOWER(first_name)")
    athletes = cursor.fetchall()

    selected_id = request.form.get("athlete_id") or request.args.get("athlete_id")
    absences = []

    if selected_id:
        # Handle deletion
        if request.method == "POST" and request.form.get("delete_id"):
            delete_id = request.form.get("delete_id")
            cursor.execute("DELETE FROM attendance WHERE id = ?", (delete_id,))
            db.commit()

        # Get absences after possible deletion
        cursor.execute("""
            SELECT id, date, notes
            FROM attendance
            WHERE athlete_id = ? AND status = 'Absent'
            ORDER BY date DESC
        """, (selected_id,))
        absences = cursor.fetchall()

    return render_template("manage_absences.html", athletes=athletes, selected_id=int(selected_id) if selected_id else None, absences=absences)


new_athlete = Athlete(first_name=first_name, last_name=last_name, team_id=int(team_id))


@app.route('/import_csv', methods=['GET', 'POST'])
def import_csv():
    
    if request.method == 'POST':
        file = request.files['file']
        if file and file.filename.endswith('.csv'):
            stream = TextIOWrapper(file.stream)
            csv_input = csv.reader(stream)
            next(csv_input)  # Skip header

            for row in csv_input:
                first_name, last_name, team_id = row
                new_athlete = Athlete(first_name=first_name, last_name=last_name, team_id=int(team_id))
                db.session.add(new_athlete)

            db.session.commit()
            flash('CSV imported successfully.')
            return redirect(url_for('home'))
        else:
            flash('Invalid file type. Please upload a .csv file.')
            return redirect(url_for('import_csv'))

    return render_template('import_csv.html')



# Database setup and teardown
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# Run the app
if __name__ == "__main__":
    app.run(debug=True)
