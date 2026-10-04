"""SQLite database: rules, addresses, default-date answers, change tests, users, sessions, saved addresses.

Built from the pipeline outputs, so the database never disagrees with the submitted JSON files.
"""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from citemap import config

DB_PATH = Path(os.environ.get("CITEMAP_DB", config.ROOT / "data" / "citemap.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, jurisdiction TEXT, level TEXT, category TEXT, status TEXT,
  citation TEXT, key_value TEXT, effective_date TEXT, conflict_flag INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS addresses (id TEXT PRIMARY KEY, street TEXT, postal_city TEXT, state TEXT, zip TEXT,
  city TEXT, year_built INTEGER, units_min INTEGER, units_max INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS answers (address_id TEXT, rule_id TEXT, result TEXT, as_of TEXT, PRIMARY KEY(address_id, rule_id, as_of));
CREATE TABLE IF NOT EXISTS changes (test_id TEXT PRIMARY KEY, json TEXT);
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, name TEXT, role TEXT,
  pw_hash TEXT, salt TEXT, created REAL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS saved (user_id INTEGER, address_id TEXT, note TEXT, created REAL, PRIMARY KEY(user_id, address_id));
CREATE TABLE IF NOT EXISTS events (ts REAL, user_id INTEGER, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_addr_street ON addresses(street);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")          # wait for a lock instead of failing
    try:
        con.execute("PRAGMA journal_mode=WAL")         # many readers + one writer at the same time
    except sqlite3.OperationalError:
        pass
    con.executescript(SCHEMA)
    return con


class ThreadConnection:
    """One SQLite connection per thread (a shared connection is not safe under concurrent requests).
    Behaves like a sqlite3.Connection, including `with CON:` transactions."""

    def __init__(self):
        import threading
        self._local = threading.local()

    def _c(self) -> sqlite3.Connection:
        c = getattr(self._local, "con", None)
        if c is None:
            c = self._local.con = connect()
        return c

    def __getattr__(self, name):
        return getattr(self._c(), name)

    def __enter__(self):
        return self._c().__enter__()

    def __exit__(self, *a):
        return self._c().__exit__(*a)


def load_outputs(con: sqlite3.Connection) -> dict:
    """(Re)load rules, addresses, default answers and change tests from outputs/*.json."""
    out = config.OUTPUTS
    rules = json.loads((out / "rules.json").read_text(encoding="utf-8"))["rules"]
    detail = json.loads((out / "lookups_detail.json").read_text(encoding="utf-8"))
    changes = json.loads((out / "changes.json").read_text(encoding="utf-8"))
    with con:
        con.execute("DELETE FROM rules"); con.execute("DELETE FROM addresses")
        con.execute("DELETE FROM answers"); con.execute("DELETE FROM changes")
        con.executemany("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (r["team_rule_id"], r["jurisdiction"], r["level"], r["category"], r["status"], r["citation"],
             r.get("key_value"), r.get("effective_date"), int(bool(r.get("conflict_flag"))), json.dumps(r)) for r in rules])
        rows, ans = [], []
        for aid, v in detail["lookups"].items():
            a, f, j = v["address"], v["facts"], v["jurisdiction"]
            rows.append((aid, a["street_address"], a["postal_city"], a["state"], a["zip"], j["city"],
                         f["year_built"], f["units_min"], f["units_max"], json.dumps(v)))
            ans += [(aid, it["team_rule_id"], it["result"], detail["as_of"]) for it in v["results"]]
        con.executemany("INSERT INTO addresses VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        con.executemany("INSERT INTO answers VALUES (?,?,?,?)", ans)
        con.executemany("INSERT INTO changes VALUES (?,?)", [(k, json.dumps(v)) for k, v in changes.items()])
    return {"rules": len(rules), "addresses": len(rows), "answers": len(ans), "changes": len(changes)}


# ---------- auth (stdlib only: PBKDF2-SHA256, random session tokens) ----------
def hash_pw(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return h, salt


def create_user(con, email: str, name: str, role: str, pw: str) -> int:
    h, s = hash_pw(pw)
    with con:
        cur = con.execute("INSERT INTO users(email,name,role,pw_hash,salt,created) VALUES (?,?,?,?,?,?)",
                          (email.lower().strip(), name.strip(), role, h, s, time.time()))
    return cur.lastrowid


_DUMMY = hash_pw("not-a-real-password")      # equal work for unknown emails (no timing difference)
SESSION_DAYS = 7


def check_user(con, email: str, pw: str):
    u = con.execute("SELECT * FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
    if not u:
        hash_pw(pw, _DUMMY[1])
        return None
    h, _ = hash_pw(pw, u["salt"])
    return u if secrets.compare_digest(h, u["pw_hash"]) else None


def _tok_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()   # only a hash of the session token is stored


def new_session(con, user_id: int) -> str:
    tok = secrets.token_urlsafe(32)
    with con:
        con.execute("DELETE FROM sessions WHERE created < ?", (time.time() - SESSION_DAYS * 86400,))
        con.execute("INSERT INTO sessions VALUES (?,?,?)", (_tok_hash(tok), user_id, time.time()))
    return tok


def end_session(con, token: str | None):
    if token:
        with con:
            con.execute("DELETE FROM sessions WHERE token=?", (_tok_hash(token),))


def user_for(con, token: str | None):
    if not token or len(token) > 200:
        return None
    return con.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.created >= ?",
                       (_tok_hash(token), time.time() - SESSION_DAYS * 86400)).fetchone()


def log_event(con, user_id, kind: str, detail: str = ""):
    with con:
        con.execute("INSERT INTO events VALUES (?,?,?,?)", (time.time(), user_id, kind, detail[:500]))
