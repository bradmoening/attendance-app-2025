import csv
import sqlite3

def import_roster(filename="roster.csv"):
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()

    teams_cache = {}

    with open(filename, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            first = row["First"].strip()
            last = row["Last"].strip()
            grade = int(row["Grade"])
            gender = row["Gender"].strip()
            team_name = row["Team"].strip()

            # Insert team if not already in DB
            if team_name not in teams_cache:
                cursor.execute("SELECT id FROM teams WHERE name = ?", (team_name,))
                team = cursor.fetchone()
                if not team:
                    cursor.execute("INSERT INTO teams (name) VALUES (?)", (team_name,))
                    conn.commit()
                    team_id = cursor.lastrowid
                else:
                    team_id = team[0]
                teams_cache[team_name] = team_id
            else:
                team_id = teams_cache[team_name]

            # Insert athlete
            cursor.execute('''
                INSERT INTO athletes (first_name, last_name, grade, gender, team_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (first, last, grade, gender, team_id))

    conn.commit()
    conn.close()
    print("Roster import complete.")

if __name__ == "__main__":
    import_roster()
