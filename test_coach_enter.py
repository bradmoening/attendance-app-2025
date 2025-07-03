import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect('attendance.db')
cursor = conn.cursor()
hashed = generate_password_hash("yourpassword123")
cursor.execute("INSERT INTO coaches (name, username, password, team_id) VALUES (?, ?, ?, ?)",
               ("Coach Moening", "moening", hashed, 1))
conn.commit()
conn.close()
