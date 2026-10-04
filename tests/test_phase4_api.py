"""Phase 4 tests: API, database, optional login."""
import os, tempfile, uuid
os.environ["CITEMAP_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
from fastapi.testclient import TestClient
from server.app import app

C = TestClient(app)


def test_health_and_stats():
    assert C.get("/api/health").json()["loaded"]["addresses"] == 500
    s = C.get("/api/stats").json()
    assert "not legal advice" in s["disclaimer"] and s["data"]["counts"]["rules"] >= 30


def test_search_and_lookup_default_and_future_date():
    hits = C.get("/api/addresses", params={"q": "CLINTON", "city": "Hoboken"}).json()["data"]
    assert hits
    aid = hits[0]["id"]
    now = C.get(f"/api/lookup/{aid}").json()["data"]["results"]
    later = C.get(f"/api/lookup/{aid}", params={"as_of": "2027-07-02"}).json()["data"]["results"]
    fair_now = [x for x in now if "P.L. 2026, c. 43" in x["rule"]["citation"]][0]
    fair_later = [x for x in later if "P.L. 2026, c. 43" in x["rule"]["citation"]][0]
    assert fair_now["result"] == "not_yet_effective" and fair_later["result"] == "applies"
    assert fair_now["rule"]["quoted_span"]


def test_bad_inputs():
    assert C.get("/api/lookup/NOPE").status_code == 404
    assert C.get("/api/lookup/A0001", params={"as_of": "tomorrow"}).status_code == 400


def test_changes_and_rules():
    ch = C.get("/api/changes").json()["data"]
    assert {c["test_id"] for c in ch} >= {"T1", "T2", "T3", "T4", "T5"}
    assert C.get("/api/rules", params={"category": "algorithmic_rent_setting"}).json()["data"]


def test_guest_can_use_everything_but_saving():
    assert C.get("/api/me").json()["data"] is None
    assert C.get("/api/saved").status_code == 401


def test_register_login_save_logout():
    c = TestClient(app)
    email = f"t{uuid.uuid4().hex[:6]}@example.org"
    assert c.post("/api/auth/register", json={"email": email, "password": "short"}).status_code == 400
    r = c.post("/api/auth/register", json={"email": email, "name": "Tess", "password": "longenough1", "role": "advocate"})
    assert r.status_code == 200 and c.get("/api/me").json()["data"]["role"] == "advocate"
    assert c.post("/api/saved/A0001").status_code == 200
    assert c.get("/api/saved").json()["data"][0]["address_id"] == "A0001"
    c.post("/api/auth/logout")
    assert c.get("/api/me").json()["data"] is None
    assert c.post("/api/auth/login", json={"email": email, "password": "wrong-pass"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": email, "password": "longenough1"}).status_code == 200
    assert c.post("/api/auth/register", json={"email": email, "password": "longenough1"}).status_code == 400


def test_login_rate_limit_and_session_hashing():
    import os
    from server import db
    os.environ.pop("CITEMAP_RATE_LIMIT", None)
    c2 = TestClient(app)
    codes = [c2.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 9}).status_code for _ in range(12)]
    assert codes[0] == 401 and 429 in codes
    # stored session tokens are hashes, never the cookie value
    tok = db.new_session(db.ThreadConnection(), 1)
    assert not db.ThreadConnection().execute("SELECT 1 FROM sessions WHERE token=?", (tok,)).fetchone()
    # oversized fields are rejected, not stored
    assert c2.post("/api/auth/register", json={"email": "a@b.co", "name": "x" * 5000, "password": "longenough1"}).status_code == 422
