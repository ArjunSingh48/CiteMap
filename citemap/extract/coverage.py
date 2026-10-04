"""Phase 2c: read machine-testable coverage conditions from the law text (no AI).

Each pattern returns a finding: which categories it scopes, whether it is a coverage
condition ('all') or an exemption ('exempt_any'), the conditions, and the exact evidence
text. Findings from statutes attach to rules from the SAME document (they are part of that
section); findings from city guidance pages attach to every rule of the same city and
category (coverage of a city ordinance is often explained on a separate page).
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from ..corpus import Doc, HEADER_RE
from .profile import parse_date, DATE_TXT

RENT, EVICT, DEP, FEE = "rent_increase_limits", "just_cause_eviction", "security_deposits", "application_screening_fees"
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20}


@dataclass
class Finding:
    doc_id: str
    scope: list            # categories; [] = all categories of this doc's rules
    kind: str              # 'all' | 'exempt' | 'defers_to_local'
    conds: list
    evidence: str
    cross_doc: bool        # attach to same city+category in other docs
    juris: str = ""


def _ctx(flat: str, m: re.Match, n: int = 220) -> str:
    return flat[max(0, m.start() - n): m.end() + n]


def _num(s: str) -> int | None:
    s = s.lower()
    return int(s) if s.isdigit() else WORDNUM.get(s)


def find(doc: Doc) -> list[Finding]:
    hm = HEADER_RE.match(doc.raw)
    flat = re.sub(r"\s+", " ", doc.raw[hm.end():] if hm else doc.raw)
    out: list[Finding] = []
    city_doc = "," in (doc.jurisdictions or "")

    def scope_of(ctx: str, default: list) -> list:
        c = ctx.lower()
        sc = []
        if re.search(r"rent (?:increase|control|stabiliz)|\brso\b|rent ceiling|rent cap", c):
            sc.append(RENT)
        if re.search(r"just cause|good cause|evict", c):
            sc.append(EVICT)
        if re.search(r"\brso\b|rent stabilization ordinance", c) and EVICT not in sc:
            sc.append(EVICT)                     # an RSO coverage cutoff covers its eviction rules too
        return sc or default

    # 1. certificate of occupancy issued within the previous N years -> exemption by building age
    for m in re.finditer(r"certificate of occupancy within the (?:previous|last|past) (\w+) years", flat, re.I):
        n = _num(m.group(1))
        if n:
            out.append(Finding(doc.doc_id, [], "exempt", [[{"fact": "building_age_years", "op": "<", "value": n}]],
                               m.group(0), False))
    # 2. housing produced/constructed in the last N years -> exemption (city ordinances)
    for m in re.finditer(r"(?:housing|units?|buildings?) (?:produced|constructed|built) (?:in|within) the (?:last|previous|past) (\w+) years", flat, re.I):
        n = _num(m.group(1))
        if n:
            ctx = _ctx(flat, m)
            out.append(Finding(doc.doc_id, scope_of(ctx[:260], []), "exempt",
                               [[{"fact": "building_age_years", "op": "<", "value": n}]], m.group(0), city_doc))
    # 3. built / certificate on or before a date -> coverage cutoff
    for m in re.finditer(r"(?:first )?(?:built|constructed) on or before " + DATE_TXT, flat, re.I):
        d = parse_date(m.group(0))
        ctx = _ctx(flat, m)
        out.append(Finding(doc.doc_id, scope_of(ctx, [RENT]), "all",
                           [{"fact": "year_built", "op": "<=", "value": d, "basis": "certificate_of_occupancy"}],
                           m.group(0), city_doc))
    # 4. new construction with certificate of occupancy AFTER a date is exempt
    for m in re.finditer(r"certificate of occupancy after " + DATE_TXT, flat, re.I):
        d = parse_date(m.group(0))
        ctx = flat[max(0, m.start() - 600): m.end() + 200]
        if re.search(r"exempt|not apply|not subject|newly constructed", ctx, re.I):
            sc = [RENT] if re.search(r"rent increase limitation", ctx, re.I) else scope_of(ctx, [RENT])
            if re.search(r"\bRSO\b|Rent Stabilization Ordinance", ctx) and EVICT not in sc:
                sc = sc + [EVICT]                # the RSO cutoff covers the whole ordinance, evictions included
            out.append(Finding(doc.doc_id, sc, "all",
                               [{"fact": "year_built", "op": "<=", "value": d, "basis": "certificate_of_occupancy"}],
                               m.group(0), city_doc))
    # 5. constructed after a date: 'does not apply'
    for m in re.finditer(r"(?:constructed|built) after " + DATE_TXT, flat, re.I):
        ctx = _ctx(flat, m, 160)
        if re.search(r"does not apply|not apply|exempt", ctx, re.I):
            d = parse_date(m.group(0))
            out.append(Finding(doc.doc_id, scope_of(ctx, [RENT]), "all",
                               [{"fact": "year_built", "op": "<=", "value": d, "basis": "year_built"}], m.group(0), city_doc))
    # 6. 'built before 1980' style coverage tables; if the same text dates the cutoff by a certificate of
    #    occupancy within that year ('Occupancy after June 1980'), a building from that year is undecidable
    for m in re.finditer(r"(?:properties|buildings|units) built before (\d{4})", flat, re.I):
        yr = m.group(1)
        co = re.search(r"occupancy (?:issued )?after (January|February|March|April|May|June|July|August|September|October|November|December)\s+" + yr, flat, re.I)
        if co:
            cond = {"fact": "year_built", "op": "<=", "value": parse_date(f"{co.group(1)} 1, {yr}"),
                    "basis": "certificate_of_occupancy"}
            ev = m.group(0) + " ... " + co.group(0)
        else:
            cond = {"fact": "year_built", "op": "<", "value": int(yr), "basis": "year_built"}
            ev = m.group(0)
        out.append(Finding(doc.doc_id, [RENT], "all", [cond], ev, city_doc))
    # 7. owner-occupied two-unit property exemption
    for m in re.finditer(r"two separate dwelling units within a single structure in which the owner occupied", flat, re.I):
        out.append(Finding(doc.doc_id, [], "exempt",
                           [[{"fact": "owner_occupied", "op": "==", "value": True}, {"fact": "units", "op": "<=", "value": 2}]],
                           m.group(0), False))
    # 8. single-family / condo (alienable separately) owned by a natural person
    if re.search(r"alienable separate from the title to any other dwelling unit", flat, re.I):
        m = re.search(r"alienable separate from the title to any other dwelling unit", flat, re.I)
        out.append(Finding(doc.doc_id, [], "exempt",
                           [[{"fact": "unit_type", "op": "in", "value": ["single_family", "condominium"]},
                             {"fact": "owner_type", "op": "==", "value": "natural_person"}]], m.group(0), False))
    # 9. one- or two-family dwelling exemption
    for m in re.finditer(r"(?:located )?in a one-family or two-family dwelling", flat, re.I):
        out.append(Finding(doc.doc_id, [], "exempt", [[{"fact": "units", "op": "<=", "value": 2}]], m.group(0), False))
    # 10. state rule yields to stricter / more protective local law
    for m in re.finditer(r"restricts annual increases in the rental rate to an amount less than", flat, re.I):
        out.append(Finding(doc.doc_id, [RENT], "defers_to_local", [], m.group(0), False))
    for m in re.finditer(r"local ordinance requiring just cause for termination of a residential tenancy[^.]{0,120}more protective", flat, re.I):
        out.append(Finding(doc.doc_id, [EVICT], "defers_to_local", [], m.group(0)[:200], False))
    # 10b. owner-occupied two- or three-family dwellings (NJ Anti-Eviction Act: owner-occupied, <= 2 rental units)
    for m in re.finditer(r"owner[- ]occupied (?:premises with not more than two rental units|two[- ](?:and|or) three[- ]family (?:dwellings?|buildings?|homes?))", flat, re.I):
        out.append(Finding(doc.doc_id, [EVICT], "exempt",
                           [[{"fact": "owner_occupied", "op": "==", "value": True}, {"fact": "units", "op": "<=", "value": 3}]],
                           m.group(0), False))
        break
    # 10c. screening rules limited to publicly funded / affordable housing (e.g. Fair Chance policies)
    m = re.search(r"(?:receiving|recipients? of)[^.]{0,80}?fund\w*|in affordable housing (?:decisions|units|programs)", flat, re.I)
    if m:
        out.append(Finding(doc.doc_id, ["screening_restrictions"], "all",
                           [{"fact": "publicly_funded_housing", "op": "==", "value": True}], m.group(0), False))
    # 11. small-landlord deposit exception
    for m in re.finditer(r"no more than two residential rental properties that collectively include no more than four dwelling units", flat, re.I):
        out.append(Finding(doc.doc_id, [DEP], "exempt",
                           [[{"fact": "owner_type", "op": "==", "value": "natural_person"},
                             {"fact": "landlord_unit_count", "op": "<=", "value": 4}]], m.group(0), False))
    # 12. N or more units coverage
    for m in re.finditer(r"(?:buildings?|properties|structures?) (?:with|containing|of) (\w+) or more (?:residential |dwelling |rental )?units", flat, re.I):
        n = _num(m.group(1))
        short_doc = len(flat.split()) < 3000          # single-topic law: the threshold covers the whole text
        sc = scope_of(_ctx(flat, m, 120), [])
        if n and n > 1 and (sc or short_doc):
            out.append(Finding(doc.doc_id, sc, "all",
                               [{"fact": "units", "op": ">=", "value": n}], m.group(0), False))
    for f in out:
        f.juris = (doc.jurisdictions or "").split(";")[0].strip()
    return out


NOT_REGULATED = re.compile(r"not (?:regulated by|subject to|covered by) the (?:City[’']s )?(?:RSO|Rent Stabilization Ordinance|Rent Ordinance)", re.I)


def attach(records: list[dict], findings: list[Finding], docs_text: dict | None = None) -> list[dict]:
    """Merge findings into each record's coverage_conditions (deduplicated)."""
    docs_text = docs_text or {}
    by_doc = {}
    for f in findings:
        by_doc.setdefault(f.doc_id, []).append(f)
    juris_of_doc = {r["source_doc_id"]: r["jurisdiction"] for r in records}
    for r in records:
        cc = r["coverage_conditions"]
        evid = cc.setdefault("evidence", [])
        rel = []
        for f in by_doc.get(r["source_doc_id"], []):
            if not f.scope or r["category"] in f.scope:
                rel.append(f)
        own = docs_text.get(r["source_doc_id"], "")
        excluded = r["level"] == "city" and bool(NOT_REGULATED.search(own))
        for f in findings:
            if f.cross_doc and f.doc_id != r["source_doc_id"] and r["level"] == "city" and f.juris == r["jurisdiction"] \
                    and (r["category"] in f.scope or (excluded and EVICT in f.scope)):
                if excluded:
                    # this record's law covers exactly the units the other ordinance does NOT: invert its cutoff
                    for c in f.conds:
                        if isinstance(c, dict) and c.get("fact") == "year_built" and c.get("op") in ("<=", "<"):
                            inv = dict(c, op=">" if c["op"] == "<=" else ">=")
                            if inv not in cc["all"]:
                                cc["all"].append(inv)
                                evid.append({"doc_id": f.doc_id, "text": "NOT covered by the other ordinance: " + f.evidence})
                    continue
                rel.append(f)
        for f in rel:
            if f.kind == "defers_to_local":
                cc["defers_to_local"] = True
            elif f.kind == "all":
                for c in f.conds:
                    if c not in cc["all"]:
                        cc["all"].append(c)
            elif f.kind == "exempt":
                for clause in f.conds:
                    if clause not in cc["exempt_any"]:
                        cc["exempt_any"].append(clause)
            e = {"doc_id": f.doc_id, "text": f.evidence}
            if e not in evid:
                evid.append(e)
        dated = {str(c["value"])[:4] for c in cc["all"] if c.get("fact") == "year_built" and isinstance(c.get("value"), str)}
        cc["all"] = [c for c in cc["all"] if not (c.get("fact") == "year_built" and isinstance(c.get("value"), int)
                                                    and c.get("op") == "<" and str(c["value"]) in dated)]
        if not cc.get("text"):
            cc["text"] = _describe(cc)
    return records


def _describe(cc: dict) -> str:
    parts = []
    for c in cc["all"]:
        f, op, v = c["fact"], c["op"], c["value"]
        if f == "year_built":
            parts.append(f"{'certificate of occupancy' if c.get('basis') == 'certificate_of_occupancy' else 'built'} {op} {v}")
        else:
            parts.append(f"{f.replace('_', ' ')} {op} {v}")
    s = "Covers: " + ("; ".join(parts) if parts else "residential rentals in this jurisdiction")
    if cc["exempt_any"]:
        s += f" ({len(cc['exempt_any'])} exemption(s) recorded)"
    if cc.get("defers_to_local"):
        s += "; yields where a stricter local ordinance applies"
    return s
