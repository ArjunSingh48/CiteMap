"""CiteMap API + web app (FastAPI).

Run (from the repo root):  uvicorn server.app:app --port 8000
Every answer is computed by the same deterministic engine used for lookups.json; any date can
be queried live. Login is optional: every page works as a guest.
"""
from __future__ import annotations
import json
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from citemap import config
from citemap.apply.run_lookups import lookup_one
from . import db

DISCLAIMER = "Information only - not legal advice. Check the cited source."
WEB = config.ROOT / "web"
app = FastAPI(title="CiteMap API", version="1.0", description=DISCLAIMER)
_lock = threading.Lock()
CON = db.ThreadConnection()
STATE = {"loaded": None}


def ensure_loaded():
    if STATE["loaded"] is None:
        with _lock:
            if STATE["loaded"] is None:
                STATE["loaded"] = db.load_outputs(CON)


def rules_list():
    return [json.loads(r["json"]) for r in CON.execute("SELECT json FROM rules ORDER BY id")]


def current_user(request: Request):
    return db.user_for(CON, request.cookies.get("cm_session"))


def ok(data, **extra):
    return {"disclaimer": DISCLAIMER, **extra, "data": data}


DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def valid_date(s: str) -> bool:
    """ASCII YYYY-MM-DD that is a real calendar date."""
    if not DATE.match(s or ""):
        return False
    try:
        from datetime import date as _d
        _d.fromisoformat(s)
        return True
    except ValueError:
        return False


@app.get("/api/health")
def health():
    ensure_loaded()
    return {"ok": True, "loaded": STATE["loaded"]}


@app.get("/api/stats")
def stats():
    ensure_loaded()
    q = lambda s: [dict(r) for r in CON.execute(s)]
    return ok({
        "counts": STATE["loaded"],
        "by_result": q("SELECT result, COUNT(*) n FROM answers GROUP BY result"),
        "by_city": q("SELECT city, COUNT(*) n FROM addresses GROUP BY city ORDER BY n DESC"),
        "rules_by_category": q("SELECT category, COUNT(*) n FROM rules GROUP BY category"),
        "rules_by_status": q("SELECT status, COUNT(*) n FROM rules GROUP BY status"),
        "as_of": config.DEFAULT_AS_OF,
    })


@app.get("/api/rules")
def rules(category: str | None = None, jurisdiction: str | None = None, status: str | None = None):
    ensure_loaded()
    rs = rules_list()
    if category:
        rs = [r for r in rs if r["category"] == category]
    if jurisdiction:
        rs = [r for r in rs if r["jurisdiction"] == jurisdiction]
    if status:
        rs = [r for r in rs if r["status"] == status]
    return ok(rs)


@app.get("/api/rules/{rid}")
def rule(rid: str):
    ensure_loaded()
    r = CON.execute("SELECT json FROM rules WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "rule not found")
    return ok(json.loads(r["json"]))


@app.get("/api/addresses")
def addresses(q: str = "", city: str | None = None, limit: int = 25):
    ensure_loaded()
    sql = "SELECT id, street, postal_city, state, zip, city, year_built, units_min, units_max FROM addresses WHERE 1=1"
    args = []
    if q:
        sql += " AND (street LIKE ? OR id LIKE ? OR postal_city LIKE ? OR zip LIKE ?)"
        args += [f"%{q}%"] * 4
    if city:
        sql += " AND city=?"; args.append(city)
    sql += " ORDER BY city, street LIMIT ?"; args.append(max(1, min(limit, 200)))
    return ok([dict(r) for r in CON.execute(sql, args)])


@app.get("/api/lookup/{aid}")
def lookup(aid: str, request: Request, as_of: str = config.DEFAULT_AS_OF):
    ensure_loaded()
    if not valid_date(as_of):
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    row = CON.execute("SELECT json FROM addresses WHERE id=?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404, "address not found")
    addr = json.loads(row["json"])["address"]
    rs = rules_list()
    res = lookup_one(addr, rs, as_of)
    by_id = {r["team_rule_id"]: r for r in rs}
    for it in res["results"]:
        r = by_id[it["team_rule_id"]]
        it["rule"] = {k: r.get(k) for k in ("title", "category", "jurisdiction", "level", "status", "citation", "key_value",
                                            "effective_date", "quoted_span", "source_url", "retrieval_date", "source_doc_id",
                                            "conflict_note", "interaction", "coverage_conditions", "confidence", "extracted_by",
                                            "record_type", "source_authority", "exemptions", "penalty")}
    u = current_user(request)
    if u:                                     # guests are not logged (keeps the database small and private)
        db.log_event(CON, u["id"], "lookup", f"{aid} {as_of}")
    return ok(res, as_of=as_of)


@app.get("/api/changes")
def changes():
    ensure_loaded()
    tests = {t["test_id"]: t for t in json.loads(config.CHANGE_TESTS.read_text(encoding="utf-8"))}
    out = []
    for r in CON.execute("SELECT * FROM changes ORDER BY test_id"):
        v = json.loads(r["json"])
        t = tests.get(r["test_id"], {"title": "New law (added during the event)", "type": "new_law"})
        out.append({"test_id": r["test_id"], "title": t.get("title"), "type": t.get("type"),
                    "expected": t.get("expected_behavior"), **v})
    return ok(out)


@app.get("/api/audit")
def audit(limit: int = 60):
    """Latest pipeline events, with server file paths removed."""
    p = config.AUDIT / "audit.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):] if p.exists() else []
    out = []
    for x in lines:
        try:
            e = json.loads(x)
        except ValueError:
            continue
        out.append({k: v for k, v in e.items() if not (isinstance(v, str) and ("/" in v and (v.startswith("/") or ":\\" in v)))
                    and k not in ("path", "file", "text_path")})
    return ok(out)


# ---------- auth ----------
class Register(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field("", max_length=100)
    password: str = Field(max_length=200)
    role: str = Field("renter", max_length=20)


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


_hits: dict[str, list[float]] = {}


def rate_limit(request: Request, key: str, n: int = 10, per: float = 60.0):
    """Simple per-IP limit for login / register (in memory, single process)."""
    import time
    if os.environ.get("CITEMAP_RATE_LIMIT") == "0":
        return
    ip = request.client.host if request.client else "?"
    now = time.time()
    lst = [t for t in _hits.get(f"{key}:{ip}", []) if now - t < per]
    if len(lst) >= n:
        raise HTTPException(429, "Too many attempts. Please wait a minute and try again.")
    lst.append(now)
    _hits[f"{key}:{ip}"] = lst
    if len(_hits) > 10000:
        _hits.clear()


def _set_cookie(resp: Response, tok: str, request: Request):
    # Secure whenever the site is served over https (directly or behind the host's TLS proxy)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    resp.set_cookie("cm_session", tok, httponly=True, samesite="strict", secure=secure, max_age=7 * 24 * 3600)


@app.post("/api/auth/register")
def register(body: Register, response: Response, request: Request):
    ensure_loaded()
    rate_limit(request, "register", 5)
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", body.email) or len(body.password) < 8:
        raise HTTPException(400, "Use a valid email and a password of at least 8 characters.")
    if body.role not in ("renter", "advocate", "provider"):
        raise HTTPException(400, "role must be renter, advocate or provider")
    try:
        uid = db.create_user(CON, body.email, body.name, body.role, body.password)
    except Exception:
        # same message whether or not the email exists (no account enumeration)
        raise HTTPException(400, "Could not create an account with these details. Try signing in instead.")
    _set_cookie(response, db.new_session(CON, uid), request)
    db.log_event(CON, uid, "register")
    return ok({"email": body.email.lower(), "name": body.name, "role": body.role})


@app.post("/api/auth/login")
def login(body: Login, response: Response, request: Request):
    rate_limit(request, "login", 10)
    u = db.check_user(CON, body.email, body.password)
    if not u:
        raise HTTPException(401, "Wrong email or password.")
    _set_cookie(response, db.new_session(CON, u["id"]), request)
    db.log_event(CON, u["id"], "login")
    return ok({"email": u["email"], "name": u["name"], "role": u["role"]})


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    db.end_session(CON, request.cookies.get("cm_session"))
    response.delete_cookie("cm_session")
    return ok(True)


@app.get("/api/me")
def me(request: Request):
    u = current_user(request)
    return ok({"email": u["email"], "name": u["name"], "role": u["role"]} if u else None)


@app.get("/api/saved")
def saved(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Log in to save addresses.")
    rows = CON.execute("""SELECT s.address_id, s.note, a.street, a.city, a.state FROM saved s
                          JOIN addresses a ON a.id=s.address_id WHERE s.user_id=? ORDER BY s.created DESC""", (u["id"],))
    return ok([dict(r) for r in rows])


@app.post("/api/saved/{aid}")
def save(aid: str, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Log in to save addresses.")
    ensure_loaded()
    if not CON.execute("SELECT 1 FROM addresses WHERE id=?", (aid,)).fetchone():
        raise HTTPException(404, "address not found")
    with CON:
        CON.execute("INSERT OR IGNORE INTO saved VALUES (?,?,?,strftime('%s','now'))", (u["id"], aid, ""))
    return ok(True)


@app.delete("/api/saved/{aid}")
def unsave(aid: str, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Log in first.")
    with CON:
        CON.execute("DELETE FROM saved WHERE user_id=? AND address_id=?", (u["id"], aid))
    return ok(True)


@app.post("/api/admin/reload")
def reload_outputs(request: Request):
    import os, secrets as _s
    tok = os.environ.get("CITEMAP_ADMIN_TOKEN")
    if not tok or not _s.compare_digest(request.headers.get("X-Admin-Token", ""), tok):
        raise HTTPException(403, "admin token required")
    with _lock:
        STATE["loaded"] = db.load_outputs(CON)
    return ok(STATE["loaded"])


# ---------- web app ----------
if (WEB / "static").exists():
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
if (WEB / "data").exists():
    app.mount("/data", StaticFiles(directory=WEB / "data"), name="data")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.exception_handler(Exception)
async def fail_open(request: Request, exc: Exception):
    # never show a stack trace to a user; the UI falls back to the saved JSON
    return JSONResponse({"disclaimer": DISCLAIMER, "error": "Something went wrong on the server. Showing saved results instead."},
                        status_code=500)
