"""Randomized 'try to break everything' harness.

  python3 tests/stress/run_stress.py --n 100 --seed 1 [--ui]

Each generated case is one test. A test FAILS if it crashes, returns a server error (5xx),
breaks an invariant, or leaves outputs invalid. Results grouped by area, with examples.
"""
from __future__ import annotations
import os
os.environ.setdefault("CITEMAP_RATE_LIMIT", "0")   # the fuzzer sends thousands of logins from one IP
import argparse
import json
import os
import random
import string
import sys
import tempfile
import traceback
from collections import Counter, defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("CITEMAP_DB", os.path.join(tempfile.mkdtemp(), "stress.db"))

from citemap import config, schema  # noqa: E402
from citemap.corpus import load_manifest, docs_with_text, Doc  # noqa: E402
from citemap.apply.facts import load_addresses, facts_for, Facts  # noqa: E402
from citemap.apply.engine import decide_address  # noqa: E402
from citemap.apply.run_lookups import lookup_one  # noqa: E402
from citemap.resolve.jurisdiction import resolve, Jurisdiction  # noqa: E402
from citemap.extract import verify  # noqa: E402
from citemap.extract.heuristic import extract_doc_heuristic, key_value  # noqa: E402
from citemap.extract.profile import profile  # noqa: E402

VALID = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}
RULES = json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))["rules"]
BY_ID = {r["team_rule_id"]: r for r in RULES}
ADDR = load_addresses()
LOOK = json.loads((config.OUTPUTS / "lookups.json").read_text(encoding="utf-8"))
DOCS = docs_with_text()

JUNK = ["", " ", "'", "\"", "' OR 1=1 --", "<script>alert(1)</script>", "../../etc/passwd", "%00", "😀🏠",
        "A" * 5000, "Ñandú ü ß", "‮", "null", "undefined", "NaN", "-1", "0", "1e309", "{}", "[]", "\n\t",
        "A0001;DROP TABLE users", "a" * 300 + "@x.io", "🏠@🏠.🏠"]


def rnd_date(rng):
    roll = rng.random()
    if roll < 0.6:
        return (date(2018, 1, 1) + timedelta(days=rng.randint(0, 365 * 12))).isoformat()
    if roll < 0.8:
        return rng.choice(["2025-12-31", "2026-01-01", "2026-01-02", "2026-10-01", "2027-06-30", "2027-07-01",
                           "2027-07-02", "1979-06-13", "1978-10-01", "2000-02-29", "0001-01-01", "9999-12-31"])
    return rng.choice(["2026-02-30", "2026-13-01", "tomorrow", "", "2026/10/01", "20261001", "2026-1-1",
                       "' or 1=1", "9999-99-99", "2026-10-01T00:00", "-2026-01-01", "２０２６-１０-０１"])


def is_valid_iso(s):
    try:
        date.fromisoformat(s)
        return len(s) == 10 and s.isascii()
    except Exception:
        return False


# ------------------------------------------------------------------ test areas
class Ctx:
    def __init__(self):
        from fastapi.testclient import TestClient
        from server.app import app
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.get("/api/health")


def t_api_lookup(rng, ctx):
    aid = rng.choice([r["address_id"] for r in ADDR] + JUNK[:8])
    d = rnd_date(rng)
    r = ctx.client.get(f"/api/lookup/{aid}", params={"as_of": d})
    assert r.status_code < 500, f"5xx for {aid!r} {d!r}"
    known = aid in {x["address_id"] for x in ADDR}
    if known and is_valid_iso(d):
        assert r.status_code == 200, f"{r.status_code} for valid {aid} {d}"
        body = r.json()["data"]
        for it in body["results"]:
            assert it["result"] in VALID
            assert it["rule"]["citation"] in it["explanation"]
    elif not known:
        assert r.status_code in (404, 400, 422), r.status_code
    else:
        assert r.status_code in (400, 422), f"invalid date accepted: {d!r} -> {r.status_code}"


def t_api_search(rng, ctx):
    q = rng.choice(JUNK + ["Mission", "clinton", "021", "A00", "ST", "%", "_"])
    params = {"q": q, "limit": rng.choice([-5, 0, 1, 25, 200, 100000, "x"])}
    if rng.random() < 0.3:
        params["city"] = rng.choice(["Boston", "Nowhere", "' OR 1=1", ""])
    r = ctx.client.get("/api/addresses", params=params)
    assert r.status_code < 500, f"5xx search {params}"
    if r.status_code == 200:
        assert isinstance(r.json()["data"], list) and len(r.json()["data"]) <= 200


def t_api_misc(rng, ctx):
    path = rng.choice(["/api/health", "/api/stats", "/api/rules", "/api/changes", "/api/audit", "/api/me",
                       "/api/rules/r-0001", "/api/rules/NOPE", "/api/saved", "/api/nothing", "/data/rules.json",
                       "/static/app.js", "/", "/static/../server/db.py", "/api/audit?limit=-1",
                       "/api/audit?limit=999999", "/api/rules?category=" + rng.choice(JUNK),
                       "/api/rules?jurisdiction=Boston, MA", "/api/admin/reload"])
    method = rng.choice(["GET", "GET", "GET", "POST", "DELETE", "PUT"])
    from urllib.parse import quote
    path = quote(path, safe="/?=&:,-._~")          # the HTTP client itself refuses raw control characters
    r = ctx.client.request(method, path)
    assert r.status_code < 500, f"5xx {method} {path}"
    if path == "/static/../server/db.py":
        assert "CREATE TABLE" not in r.text, "path traversal leaked source"


def t_api_auth(rng, ctx):
    from fastapi.testclient import TestClient
    from server.app import app
    c = TestClient(app, raise_server_exceptions=False)
    kind = rng.choice(["bad_register", "bad_login", "flow", "save_noauth", "weird_json"])
    if kind == "bad_register":
        payload = {k: rng.choice(JUNK + [123, None, [], {"a": 1}]) for k in rng.sample(["email", "password", "name", "role"], rng.randint(0, 4))}
        r = c.post("/api/auth/register", json=payload)
        assert r.status_code < 500 and r.status_code != 200 or ("@" in str(payload.get("email")) and len(str(payload.get("password", ""))) >= 8), f"register {payload} -> {r.status_code}"
        assert r.status_code < 500, f"5xx register {payload}"
    elif kind == "bad_login":
        r = c.post("/api/auth/login", json={"email": rng.choice(JUNK), "password": rng.choice(JUNK)})
        assert r.status_code in (401, 422), f"login junk -> {r.status_code}"
    elif kind == "flow":
        em = "u" + "".join(rng.choices(string.ascii_lowercase, k=10)) + "@test.org"
        pw = "".join(rng.choices(string.printable[:94], k=rng.randint(8, 40)))
        assert c.post("/api/auth/register", json={"email": em, "password": pw, "name": rng.choice(JUNK[:12])[:100], "role": rng.choice(["renter", "advocate", "provider"])}).status_code == 200
        assert c.get("/api/me").json()["data"]["email"] == em
        aid = rng.choice(ADDR)["address_id"]
        assert c.post(f"/api/saved/{aid}").status_code == 200
        assert c.post(f"/api/saved/{aid}").status_code == 200          # idempotent
        assert any(s["address_id"] == aid for s in c.get("/api/saved").json()["data"])
        bad = c.post(f"/api/saved/{rng.choice(JUNK[1:6])}")
        assert bad.status_code < 500, "saving junk id -> 5xx"
        assert c.delete(f"/api/saved/{aid}").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/me").json()["data"] is None
        assert c.post("/api/auth/login", json={"email": em.upper(), "password": pw}).status_code == 200, "email case-insensitive login"
    elif kind == "save_noauth":
        assert c.post("/api/saved/A0001").status_code == 401
        c.cookies.set("cm_session", rng.choice([j for j in JUNK if j.isascii() and j.strip()]))
        assert c.get("/api/me").status_code < 500 and c.get("/api/me").json()["data"] is None
    else:
        r = c.post("/api/auth/login", content=rng.choice([b"{", b"not json", b"[]", b'{"email":1}', b"\xff\xfe"]),
                   headers={"Content-Type": "application/json"})
        assert r.status_code < 500, f"5xx weird json {r.status_code}"


def t_engine_invariants(rng, ctx):
    row = rng.choice(ADDR)
    d = rnd_date(rng)
    if not is_valid_iso(d):
        d = "2026-10-01"
    f, j = facts_for(row), resolve(row)
    ds = decide_address(RULES, f, j, d)
    ds2 = decide_address(RULES, f, j, d)
    assert [(x.team_rule_id, x.result) for x in ds] == [(x.team_rule_id, x.result) for x in ds2], "non-deterministic"
    ids = set()
    for x in ds:
        r = BY_ID[x.team_rule_id]
        assert x.result in VALID
        assert x.team_rule_id not in ids, "duplicate rule in one answer"
        ids.add(x.team_rule_id)
        assert r["status"] != "failed", "failed law listed"
        if r["status"] == "pending":
            assert x.result == "pending", "pending bill not reported as pending"
        if r["level"] == "city":
            assert r["jurisdiction"] == f"{j.city}, {j.state}", "city rule leaked"
        else:
            assert r["jurisdiction"] == j.state, "state rule leaked"
        eff = verify.date_floor(r.get("effective_date"))
        if eff and eff > d and r["status"] not in ("pending", "failed"):
            assert x.result == "not_yet_effective", f"{r['citation']} before {eff} on {d} -> {x.result}"
        if x.result == "superseded":
            assert x.superseded_by and all(BY_ID[s]["level"] == "city" for s in x.superseded_by)
        if x.result == "unknown":
            assert x.missing_facts or any(isinstance(rr, tuple) and rr[0] == "depends_on_local_rule" for rr in x.reasons), "unknown without reason"


def t_lookup_matches_file(rng, ctx):
    row = rng.choice(ADDR)
    live = lookup_one(row, RULES, LOOK["as_of"])
    file_items = LOOK["lookups"][row["address_id"]]
    a = sorted((i["team_rule_id"], i["result"]) for i in live["results"])
    b = sorted((i["team_rule_id"], i["result"]) for i in file_items)
    assert a == b, f"{row['address_id']}: live != lookups.json"


def t_weird_facts(rng, ctx):
    row = dict(rng.choice(ADDR))
    for k in rng.sample(["year_built", "units", "use_code", "use_description", "zip", "postal_city", "state", "source_dataset"], rng.randint(1, 4)):
        row[k] = rng.choice(JUNK + ["-5", "3000", "1600", "99999", "0", "1.5", "  12 ", "abc", "5+", "1-2"])
    try:
        f = facts_for(row)
        j = resolve(row)
    except Exception as e:
        raise AssertionError(f"facts/resolve crashed on {row}: {e}")
    if f.year_built is not None:
        assert 1700 <= f.year_built <= 2030
    if f.units_min is not None:
        assert f.units_min >= 0
    ds = decide_address(RULES, f, j, "2026-10-01")
    assert all(x.result in VALID for x in ds)


def t_quote_locate(rng, ctx):
    d = rng.choice(DOCS)
    raw = d.raw
    if rng.random() < 0.6:
        a = rng.randint(0, max(0, len(raw) - 200))
        q = raw[a:a + rng.randint(25, 180)]
        q2 = q
        if rng.random() < 0.5:   # collapse whitespace / smart quotes like a model would
            q2 = " ".join(q.split()).replace("'", "’")
        span, how = verify.locate_quote(raw, q2)
        if len(" ".join(q.split())) >= 12:
            assert span is not None, f"real quote not found ({how}) in {d.doc_id}"
            assert span in raw
    else:
        fake = "".join(rng.choices(string.ascii_letters + "  ", k=rng.randint(30, 120)))
        span, how = verify.locate_quote(raw, fake)
        assert span is None or span in raw, "located a span that is not in the document"


def t_extractor_mutation(rng, ctx):
    d = rng.choice(DOCS)
    raw = d.raw
    mode = rng.choice(["truncate", "shuffle_lines", "garbage", "empty", "unicode", "dup", "header_only"])
    if mode == "truncate":
        raw = raw[: rng.randint(0, len(raw))]
    elif mode == "shuffle_lines":
        lines = raw.split("\n"); head, body = lines[:2], lines[2:]; rng.shuffle(body); raw = "\n".join(head + body)
    elif mode == "garbage":
        raw = raw[:200] + "".join(rng.choices(string.printable + "§¶•“”’—", k=rng.randint(10, 3000)))
    elif mode == "empty":
        raw = ""
    elif mode == "unicode":
        raw = raw.replace(" ", " ", rng.randint(0, 50)).replace("'", "’")
    elif mode == "dup":
        raw = raw + "\n" + raw
    else:
        raw = raw[:120]
    nd = Doc(d.doc_id, d.jurisdictions, d.url, d.source_type, d.capture, d.retrieved_at, d.status, d.text_path, raw=raw)
    try:
        profile(nd)
        recs = extract_doc_heuristic(nd)
    except Exception as e:
        raise AssertionError(f"extractor crashed on {mode} {d.doc_id}: {e!r}")
    for r in recs:
        assert r["quoted_span"] in raw, "quote not in mutated text"
        r2 = dict(r); r2["team_rule_id"] = "r-x"
        assert schema.is_valid(r2), schema.errors(r2)[:1]


def t_key_value(rng, ctx):
    s = rng.choice(["may not exceed one and one-half times one month’s rent", "5 percent plus the percentage change in the cost of living, or 10 percent",
                    "fee not to exceed $50", "shall not exceed 2 months' rent", "is 1.6%", "", "%%%", "$", "month's rent",
                    "".join(rng.choices(string.printable, k=rng.randint(0, 400)))])
    cat = rng.choice(config.CATEGORIES)
    out = key_value(s, cat)
    assert out is None or isinstance(out, str)


def t_cli(rng, ctx):
    import subprocess
    tmp = tempfile.mkdtemp()
    choice = rng.choice(["missing_file", "empty_file", "binary_file", "lookup_bad", "lookup_ok", "stats"])
    env = {**os.environ, "PYTHONPATH": ROOT}
    if choice == "missing_file":
        cmd = [sys.executable, "-m", "citemap.cli", "lookup", "NOPE"]
    elif choice in ("lookup_bad",):
        cmd = [sys.executable, "-m", "citemap.cli", "lookup", rng.choice(["NOPE", "A0001", "A9999"]), "--as-of", rnd_date(rng)]
    elif choice == "lookup_ok":
        cmd = [sys.executable, "-m", "citemap.cli", "lookup", rng.choice(ADDR)["address_id"]]
    elif choice == "stats":
        cmd = [sys.executable, "-m", "citemap.cli", "stats"]
    else:
        p = os.path.join(tmp, "x.txt")
        open(p, "wb").write(b"" if choice == "empty_file" else bytes(rng.getrandbits(8) for _ in range(2000)))
        cmd = [sys.executable, "-c", "from citemap.extract.profile import profile; from citemap.corpus import Doc; "
               f"raw=open({p!r},encoding='utf-8',errors='replace').read(); "
               "from citemap.extract.heuristic import extract_doc_heuristic; "
               "d=Doc('N-x','Cambridge, MA','file://x','official','yes','', 'ok', None, raw=raw); profile(d); print(len(extract_doc_heuristic(d)))"]
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert "Traceback" not in r.stderr, f"{choice}: traceback\n{r.stderr[-600:]}"


def t_concurrency(rng, ctx):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from server.app import app

    def one(k):
        c = TestClient(app, raise_server_exceptions=False)
        if k % 3 == 0:
            em = f"c{k}{rng.randint(0, 10**9)}@load.test"
            r1 = c.post("/api/auth/register", json={"email": em, "password": "password123"})
            r2 = c.post(f"/api/saved/{ADDR[k % 500]['address_id']}")
            return r1.status_code, r2.status_code
        r = c.get(f"/api/lookup/{ADDR[(k * 7) % 500]['address_id']}", params={"as_of": rng.choice(["2026-10-01", "2027-07-02"])})
        return r.status_code, 200
    with ThreadPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(one, range(rng.randint(12, 36))))
    bad = [x for x in res if x[0] >= 500 or x[1] >= 500]
    assert not bad, f"{len(bad)} server errors under concurrent load: {bad[:3]}"


TEMPLATES = [
    ("No landlord shall charge an application fee in excess of {money} per applicant.", "application_screening_fees"),
    ("A landlord shall not demand a security deposit in excess of {months} month's rent.", "security_deposits"),
    ("It shall be unlawful for a landlord to use an algorithmic device to set rents for residential units.", "algorithmic_rent_setting"),
    ("A landlord shall not terminate a tenancy without just cause.", "just_cause_eviction"),
    ("Annual rent increases shall not exceed {pct} percent.", "rent_increase_limits"),
]


def t_new_law_pipeline(rng, ctx):
    import shutil, subprocess
    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, "citemap")
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "cache", "audit", "*.db", ".venv"))
    city, st = rng.choice([("Cambridge", "MA"), ("Boston", "MA"), ("Newark", "NJ"), ("Berkeley", "CA"), ("San Diego", "CA")])
    tpl, cat = rng.choice(TEMPLATES)
    eff = (date(2026, 10, 2) + timedelta(days=rng.randint(0, 900))).strftime("%B %-d, %Y")
    units = rng.choice([None, 2, 4, 10])
    body = (f"CITY OF {city.upper()}\nORDINANCE NO. 2026-{rng.randint(10, 99)}\nAN ORDINANCE AMENDING CHAPTER 8.{rng.randint(10, 99)}\n\n"
            f"Section 1. Section 8.{rng.randint(10, 99)}.0{rng.randint(10, 99)} is added as follows:\n"
            + tpl.format(money=f"${rng.randint(10, 90)}", months=rng.choice(["one", "two"]), pct=rng.randint(2, 9))
            + (f"\nThis section applies to residential buildings containing {units} or more units." if units else "")
            + f"\n\nSection 2. This ordinance shall take effect on {eff}.\n")
    if rng.random() < 0.15:
        body = rng.choice(["", "\n\n\n", "lorem ipsum " * 50, "€€€ ☃ \x00 garbage"])
    p = os.path.join(tmp, "ord.txt"); open(p, "w").write(body)
    env = {**os.environ, "PYTHONPATH": dst, "CITEMAP_DB": os.path.join(tmp, "x.db")}
    r = subprocess.run([sys.executable, "-m", "citemap.cli", "add-law", p, "--jurisdiction", f"{city}, {st}"],
                       cwd=dst, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "Traceback" not in r.stderr, f"add-law failed: {r.stderr[-400:]}"
    out = os.path.join(dst, "outputs")
    rules = json.load(open(os.path.join(out, "rules.json")))["rules"]
    lk = json.load(open(os.path.join(out, "lookups.json")))
    ch = json.load(open(os.path.join(out, "changes.json")))
    assert len(lk["lookups"]) == 500 and {"T1", "T2", "T3", "T4", "T5"} <= set(ch)
    for rr in rules:
        assert schema.is_valid(rr), schema.errors(rr)[:1]
    new = [rr for rr in rules if rr["source_doc_id"].startswith("N-")]
    if new:
        assert all(rr["jurisdiction"] in (f"{city}, {st}", st) for rr in new), "new law attached to wrong city"
        cities = {resolve(a).city for a in ADDR if a["address_id"] in set(ch.get("T6", {}).get("affected_address_ids", []))}
        assert cities <= {city}, f"T6 leaked to {cities}"
        assert all(rr["quoted_span"] in body for rr in new), "new-law quote not in the file"
    shutil.rmtree(tmp, ignore_errors=True)


AREAS = [  # (weight, name, fn)
    (3, "concurrency", t_concurrency), (2, "new_law_pipeline", t_new_law_pipeline),
    (14, "api_lookup", t_api_lookup), (8, "api_search", t_api_search), (8, "api_misc", t_api_misc),
    (8, "api_auth", t_api_auth), (16, "engine_invariants", t_engine_invariants), (8, "lookup_vs_file", t_lookup_matches_file),
    (8, "weird_facts", t_weird_facts), (10, "quote_locate", t_quote_locate), (10, "extractor_mutation", t_extractor_mutation),
    (5, "key_value", t_key_value), (3, "cli", t_cli),
]


def run(n, seed, ui=False):
    rng = random.Random(seed)
    ctx = Ctx()
    weights = [w for w, _, _ in AREAS]
    stats, fails = Counter(), defaultdict(list)
    for i in range(n):
        _, name, fn = rng.choices(AREAS, weights=weights)[0]
        stats[name] += 1
        sub = random.Random(rng.random())
        try:
            fn(sub, ctx)
        except AssertionError as e:
            fails[name].append(str(e)[:300])
        except Exception as e:
            fails[name].append("CRASH " + repr(e)[:200] + " " + traceback.format_exc().splitlines()[-3][:150])
    if ui:
        from tests.stress.ui_stress import run_ui
        u_n = max(10, n // 10)
        uf = run_ui(u_n, seed)
        stats["ui"] += u_n
        for f in uf:
            fails["ui"].append(f)
        n += u_n
    total_f = sum(len(v) for v in fails.values())
    print(f"\n=== {n} tests, {total_f} failed ({100 * total_f / n:.2f}%) seed={seed}")
    for name in sorted(stats):
        print(f"  {name:20} {stats[name]:5} run  {len(fails[name]):4} failed")
    for name, fl in fails.items():
        print(f"\n--- {name}: {len(fl)} failures, examples:")
        for x in Counter(fl).most_common(6):
            print(f"   [{x[1]}x] {x[0]}")
    return n, total_f


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ui", action="store_true")
    a = ap.parse_args()
    run(a.n, a.seed, a.ui)
