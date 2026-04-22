import sqlite3
conn = sqlite3.connect('db.sqlite3')
try:
    conn.execute("DELETE FROM django_migrations WHERE app='accounts' AND name='0002_otp_address'")
    conn.commit()
    print("Migration deleted")
except Exception as e:
    print(e)
