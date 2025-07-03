import sqlite3

def init_db():
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coaches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            team_id INTEGER,
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        athlete_id INTEGER,
        date TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (athlete_id) REFERENCES athletes(id)
    )
''')


    cursor.execute('''
    CREATE TABLE IF NOT EXISTS athletes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        grade INTEGER NOT NULL,
        gender TEXT NOT NULL,
        team_id INTEGER,
        FOREIGN KEY (team_id) REFERENCES teams(id)
    )
''')


    conn.commit()
    conn.close()


  


# Call this once to initialize the DB
if __name__ == "__main__":
    init_db()
