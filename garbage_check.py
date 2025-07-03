import sqlite3

conn = sqlite3.connect('attendance.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT athlete_id, date, status, notes 
    FROM attendance 
    WHERE status = 'Absent'
""")

for row in cursor.fetchall():
    print(row)

conn.close()
