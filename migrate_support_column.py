import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "tickets.db")

with sqlite3.connect(DB) as conn:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    if "petugas" in cols and "support_id" not in cols:
        conn.execute("ALTER TABLE tickets RENAME COLUMN petugas TO support_id")
        print("Migrated tickets.petugas -> tickets.support_id; existing ticket data preserved.")
    elif "support_id" in cols:
        print("tickets.support_id already exists; no migration needed.")
    else:
        conn.execute("ALTER TABLE tickets ADD COLUMN support_id TEXT")
        print("Added tickets.support_id.")
    conn.commit()
