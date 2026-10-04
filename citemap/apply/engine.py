"""Phase 3a: the Decider. Pure code, no AI, no network.

For one address on one date, every rule gets one of:
  applies | unknown | superseded | not_yet_effective | pending | (omitted = does not apply)

Coverage is evaluated with THREE-VALUED logic (True / False / None=unknown):
  - a fact missing from the public data is None, never a guess
  - a year-based test on a certificate-of-occupancy date is None when the building's year is
    the cutoff year itself (the certificate could fall on either side)
  - unit counts are ranges [min, max]; a test is decided only if the whole range agrees
Missing an applicable rule is the costliest error, so whenever the data can't rule a rule
out, the answer is 'unknown' with the missing fact named, never a silent omission.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from ..extract.verify import status_as_of
from ..resolve.jurisdiction import Jurisdiction
from .facts import Facts

T, F, U = True, False, None


@dataclass
class Decision:
    team_rule_id: str
    result: str
    reasons: list = field(default_factory=list)
    missing_facts: list = field(default_factory=list)
    conflict_flag: bool = False
    superseded_by: list = field(default_factory=list)


# ---------- three-valued helpers ----------
def and3(vals):
    vals = list(vals)
    if any(v is F for v in vals):
        return F
    if any(v is U for v in vals):
        return U
    return T


def or3(vals):
    vals = list(vals)
    if any(v is T for v in vals):
        return T
    if any(v is U for v in vals):
        return U
    return F


def _cmp(a, op, b):
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "!=": a != b}[op]


def _year_of(v):
    if isinstance(v, int):
        return v, None
    s = str(v)
    return int(s[:4]), (s if len(s) == 10 else None)


def eval_cond(c: dict, f: Facts, as_of: str) -> tuple[bool | None, str | None]:
    """Returns (truth, missing_fact_name_if_unknown)."""
    fact, op, val = c["fact"], c["op"], c["value"]
    if fact == "year_built":
        if f.year_built is None:
            return U, "year_built"
        cy, cdate = _year_of(val)
        yb = f.year_built
        if cdate and c.get("basis") == "certificate_of_occupancy" or (cdate and yb == cy):
            # date cutoff: only the cutoff year itself is undecidable from a build year
            if yb == cy:
                return U, "certificate_of_occupancy_date"
            return _cmp(yb, op if op not in ("<=", ">=") else op[0], cy) if yb != cy else U, None
        return _cmp(yb, op, cy), None
    if fact == "building_age_years":
        if f.year_built is None:
            return U, "year_built"
        age = int(as_of[:4]) - f.year_built
        if abs(age - int(val)) <= 0 and op in ("<", ">="):
            return U, "certificate_of_occupancy_date"
        return _cmp(age, op, int(val)), None
    if fact in ("units", "landlord_unit_count"):
        lo = f.units_min
        hi = f.units_max if fact == "units" else None      # a landlord owns at least this building
        n = int(val)
        if lo is None and hi is None:
            return U, "units" if fact == "units" else "landlord_unit_count"
        if op in (">=", ">"):
            if lo is not None and _cmp(lo, op, n):
                return T, None
            if hi is not None and not _cmp(hi, op, n):
                return F, None
        elif op in ("<=", "<"):
            if hi is not None and _cmp(hi, op, n):
                return T, None
            if lo is not None and not _cmp(lo, op, n):
                return F, None
        elif op == "==" and lo is not None and lo == hi:
            return _cmp(lo, op, n), None
        return U, "units" if fact == "units" else "landlord_unit_count"
    if fact == "unit_type":
        known = "multifamily" if (f.units_min or 0) >= 2 else None
        if known is None:
            return U, "unit_type"
        vals = val if isinstance(val, list) else [val]
        if op == "in":
            return known in vals, None
        return _cmp(known, op, val), None
    # owner_type, owner_occupied: never in the public data
    return U, fact


def coverage(rule: dict, f: Facts, as_of: str) -> tuple[bool | None, list, list]:
    cc = rule.get("coverage_conditions") or {}
    if not isinstance(cc, dict):
        return T, [], []
    reasons = []
    vals, all_missing = [], []
    for c in cc.get("all", []):
        v, m = eval_cond(c, f, as_of)
        vals.append(v)
        if v is U and m:
            all_missing.append(m)
        reasons.append((c, v))
    cov = and3(vals) if vals else T
    ex_vals, ex_missing = [], []
    for clause in cc.get("exempt_any", []):
        cv, cm = [], []
        for c in clause:
            v, m = eval_cond(c, f, as_of)
            cv.append(v)
            if v is U and m:
                cm.append(m)
        cl = and3(cv)
        ex_vals.append(cl)
        if cl is U:                      # only an undecided exemption can make the answer depend on a fact
            ex_missing += cm
    exempt = or3(ex_vals) if ex_vals else F
    if exempt is T:
        return F, ["exempt"], []
    if cov is F:
        return F, reasons, []
    if cov is T and exempt is F:
        return T, reasons, []
    # undecided: keep only the missing facts that actually matter
    missing = (all_missing if cov is U else []) + (ex_missing if exempt is U else [])
    return U, reasons, sorted(set(missing))


def in_jurisdiction(rule: dict, j: Jurisdiction) -> bool:
    if rule["level"] == "state":
        return rule["jurisdiction"] == j.state
    return bool(j.city) and rule["jurisdiction"] == f"{j.city}, {j.state}"


def decide_address(rules: list[dict], f: Facts, j: Jurisdiction, as_of: str) -> list[Decision]:
    out: dict[str, Decision] = {}
    for r in rules:
        if not in_jurisdiction(r, j) or r["status"] == "failed":
            continue
        st = status_as_of(r["status"], r.get("effective_date"), as_of)
        cov, reasons, missing = coverage(r, f, as_of)
        if cov is F:
            continue
        if r.get("text_in_corpus") is False:      # named in the sources, text not available: never guessed
            cov, missing = U, ["local_ordinance_text"]
        if st == "in_force_uncertain":            # only a partial effective date in the query's own month/year
            cov, missing = U, sorted(set(missing + ["exact_effective_date"]))
        if st == "pending":
            res = "pending"
        elif st == "not_yet_effective":
            res = "not_yet_effective"
        elif cov is U:
            res = "unknown"
        else:
            res = "applies"
        flag = bool(r.get("conflict_flag"))
        if flag and r.get("conflict_jurisdictions") is not None and r["level"] == "state":
            flag = f"{j.city}, {j.state}" in r["conflict_jurisdictions"]   # only where a local rule may conflict
        out[r["team_rule_id"]] = Decision(r["team_rule_id"], res, reasons, missing, flag)
    # precedence: a state rule that defers to stricter local law yields where a local rule applies
    by_id = {r["team_rule_id"]: r for r in rules}
    for rid, d in list(out.items()):
        loc = by_id[rid]
        if loc["level"] != "city" or not loc.get("overrides"):
            continue
        for sid in loc["overrides"]:
            sd = out.get(sid)
            if not sd or sd.result not in ("applies", "unknown"):
                continue
            if d.result == "applies" and sd.result == "applies":   # superseded only if the state rule is known to cover
                sd.result = "superseded"
                sd.superseded_by.append(rid)
            elif d.result == "unknown" and sd.result == "applies":
                sd.result = "unknown"
                sd.missing_facts = sorted(set(sd.missing_facts + d.missing_facts))
                sd.reasons.append(("depends_on_local_rule", rid))
    return list(out.values())
