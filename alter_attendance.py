import sqlite3

conn = sqlite3.connect('attendance.db')
cursor = conn.cursor()
cursor.execute("ALTER TABLE attendance ADD COLUMN notes TEXT")
conn.commit()
conn.close()
