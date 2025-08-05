from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from io import TextIOWrapper
import csv
import datetime
import os

app = Flask(__name__)
app.secret_key = 'boomer'

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///test_local.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Models
class Athlete(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)

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
    if request.method == "POST":
        athlete_id = request.form.get("athlete_id")
        note = request.form.get("note", "")
        if athlete_id:
            record = Attendance.query.filter_by(athlete_id=athlete_id, date=today).first()
            if record:
                record.status = "Present"
                record.notes = note
            else:
                db.session.add(Attendance(athlete_id=athlete_id, date=today, status="Absent", notes=note))
            db.session.commit()
        return redirect(url_for('attendance', team_id=request.args.get("team_id")))

    selected_team_id = request.args.get("team_id")
    athletes = Athlete.query.filter_by(team_id=selected_team_id).order_by(Athlete.last_name).all() if selected_team_id else Athlete.query.order_by(Athlete.last_name).all()
    attendance_records = Attendance.query.filter_by(date=today).all()
    attendance_data = {r.athlete_id: r.status for r in attendance_records}
    notes_data = {r.athlete_id: r.notes for r in attendance_records}
    present_count = sum(1 for s in attendance_data.values() if s == "Present")
    absent_count = sum(1 for s in attendance_data.values() if s == "Absent")
    unmarked_count = len(athletes) - (present_count + absent_count)
    teams = Team.query.order_by(Team.name).all()
    return render_template("attendance.html", athletes=athletes, attendance=attendance_data, notes=notes_data, teams=teams, selected_team_id=int(selected_team_id) if selected_team_id else None, date=today, present_count=present_count, absent_count=absent_count, unmarked_count=unmarked_count)

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

@app.route("/seed_teams")
def seed_teams():
    if not Team.query.first():
        for name in ["Undercut", "Chicane", "Box Box", "Push Mode"]:
            db.session.add(Team(name=name))
        db.session.commit()
        return "✅ Teams seeded!"
    return "⚠️ Teams already exist."

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("✅ Tables created")
    app.run(debug=True)
