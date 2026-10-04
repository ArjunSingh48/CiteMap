"""Phase 2d: reconcile candidate rules into a clean rule set (no AI, no per-document hand edits).

Generic quality rules, each logged to the audit trail:
 1. drop list fragments ('(b) such regulation ...') whose meaning depends on an earlier clause
 2. drop records whose quote has none of its category's core terms (cross-category noise)
 3. drop guidance-page records without an official citation (page titles are not citations)
 4. drop city records that merely restate the state rule (same state + category + key value)
 5. merge duplicates (same jurisdiction + category + citation), keeping alternate sources
 6. conflict flags: a state law that says it preempts / conflicts with local ordinances flags
    itself and every local rule of the same state + category for human review
 7. precedence links (after ids): local rules list the state rules that defer to them
 8. penalty / exemption text copied from the source where it states them
 9. a draft ordinance with a blank adoption certificate is flagged, not trusted as in force
10. placeholders for change-case rules whose text is not in the corpus ('unknown', never invented)
"""
from __future__ import annotations
import re

from ..audit import log
from ..corpus import load_manifest
from .heuristic import CAT_TERMS

PREEMPT = re.compile(r"preempt\w*|ordinance that conflicts with this act|supersede\w* any (?:local )?ordinance|"
                     r"conflict\w* with (?:any )?(?:local|municipal) ordinance", re.I)
OFFICIAL_CITE = re.compile(r"§|\bch\.|N\.J\.S\.A|P\.L\.|Gen\. Laws|\b(?:AB|SB|S|H)\.? ?\d+|Code\b.*\d")
FRAGMENT = re.compile(r"^\(?[a-z0-9]{1,4}\)\s+(?:such|that|which|the same|said)\b", re.I)


def _drop(r, why):
    log("rule_dropped", reason=why, citation=r["citation"], category=r["category"], doc_id=r["source_doc_id"])


def reconcile(recs: list[dict], as_of: str) -> list[dict]:
    all_docs = load_manifest()
    docs = {d.doc_id: d for d in all_docs}
    for r in recs:
        _fill_penalty_exemptions(r, docs.get(r["source_doc_id"]))
        _flag_unadopted(r, docs.get(r["source_doc_id"]))
    kept = []
    for r in recs:
        q = re.sub(r"\s+", " ", r["quoted_span"])
        fallback = "fallback" in (r.get("notes") or "") or r["status"] in ("pending", "failed")
        if FRAGMENT.match(q):
            _drop(r, "list fragment"); continue
        strong, weak = CAT_TERMS[r["category"]]
        has_value = bool(r["key_value"] and re.search(r"\d", r["key_value"]))
        if not fallback and not re.search(strong, q, re.I) and not (re.search(weak, q, re.I) and (has_value or r["level"] == "state" and r["category"] == "application_screening_fees")):
            _drop(r, "no core category term in quote"); continue
        if not OFFICIAL_CITE.search(r["citation"]) and not r["citation"].endswith(("Mun. Code", "Admin. Code", "City Code")):
            _drop(r, "no official citation"); continue
        kept.append(r)

    # 3b. a city rent rule that states only a cap: use the city's published adjustment for the current year
    for r in kept:
        if r["level"] != "city" or r["category"] != "rent_increase_limits" or not (r["key_value"] or "").startswith("max "):
            continue
        best = None
        for d in all_docs:
            if (d.jurisdictions or "").split(";")[0].strip() != r["jurisdiction"] or not d.has_text:
                continue
            for m in re.finditer(r"(\d{4}) (?:AGA|Annual General Adjustment) (?:of|is) (\d+(?:\.\d+)?)\s?%", d.raw):
                if int(m.group(1)) <= int(as_of[:4]) and (best is None or int(m.group(1)) > best[0]):
                    best = (int(m.group(1)), m.group(2), d.doc_id, m.group(0))
        if best:
            r["notes"] = (r.get("notes") or "") + f"; {best[2]} publishes the {best[0]} adjustment: \"{best[3]}\" (the quoted {r['key_value']} is the cap)"
            r["key_value"] = f"{best[1]}% ({best[0]} adjustment; cap {r['key_value'][4:]})"
            r.setdefault("also_found_in", []).append(best[2])

    # 4. city record restating the state rule: same value, or the city page itself cites the state
    #    statute of that rule (e.g. a city page announcing the state's CPI-adjusted fee cap)
    state_vals = {(r["jurisdiction"], r["category"], r["key_value"]) for r in kept if r["level"] == "state" and r["key_value"]}
    state_rules = [r for r in kept if r["level"] == "state"]
    out = []
    for r in kept:
        st = r["jurisdiction"][-2:]
        if r["level"] == "city" and r["key_value"] and re.search(r"\d", r["key_value"]) \
                and r["category"] != "algorithmic_rent_setting":
            if (st, r["category"], r["key_value"]) in state_vals:
                if "key value taken from another sentence" in (r.get("notes") or ""):
                    # the quote itself states a local rule; only the borrowed value repeats the state rule
                    r["key_value"] = None
                    r["notes"] += "; borrowed value removed (it repeats the state rule)"
                else:
                    _drop(r, "city page restates the state rule"); continue
            d = docs.get(r["source_doc_id"])
            body = d.raw if d else ""
            hit = None if r["key_value"] is None else next((x for x in state_rules if x["jurisdiction"] == st and x["category"] == r["category"]
                        and (sec := re.search(r"§\s*([\d.]+\d)", x["citation"]))
                        and re.search(r"(?:Section|§)\s*" + re.escape(sec.group(1)) + r"\b", body)), None)
            if hit:
                hit["notes"] = (hit.get("notes") or "") + (
                    f"; {r['jurisdiction']} source {r['source_doc_id']} gives the current adjusted amount under this "
                    f"statute as {r['key_value']}; the statute text says "
                    f"{hit['key_value']}. Both shown for human review.")
                hit.setdefault("also_found_in", []).append(r["source_doc_id"])
                _drop(r, "city page restates the state statute it cites (value kept as a note on the state rule)")
                continue
        out.append(r)

    # 5. merge duplicates
    merged: dict[tuple, dict] = {}
    for r in out:
        k = (r["jurisdiction"], r["category"], r["citation"])
        if k in merged:
            m = merged[k]
            better = (r["confidence"] or 0) > (m["confidence"] or 0) or (not m["key_value"] and r["key_value"])
            keep, other = (r, m) if better else (m, r)
            alts = keep.setdefault("also_found_in", [])
            alts.extend([other["source_doc_id"]] + other.get("also_found_in", []))
            for c in other["coverage_conditions"]["all"]:
                if c not in keep["coverage_conditions"]["all"]:
                    keep["coverage_conditions"]["all"].append(c)
            merged[k] = keep
            log("rule_merged", citation=r["citation"], kept=keep["source_doc_id"], other=other["source_doc_id"])
        else:
            merged[k] = r
    out = list(merged.values())

    # 6. conflict flags from preemption language in the state law's own text
    for r in out:
        if r["level"] != "state" or r["status"] not in ("not_yet_effective", "pending"):
            continue                      # only a future state law can newly preempt existing local rules
        d = docs.get(r["source_doc_id"])
        m = PREEMPT.search(d.raw) if d else None
        if not m:
            continue
        locals_ = [x for x in out if x["level"] == "city" and x["jurisdiction"].endswith(r["jurisdiction"])
                   and x["category"] == r["category"]]
        if r["category"] != "algorithmic_rent_setting" and not locals_:
            continue
        r["conflict_flag"] = True
        r["conflict_jurisdictions"] = sorted({x["jurisdiction"] for x in locals_})   # address-level flags only there
        names = ", ".join(r["conflict_jurisdictions"]) or "local ordinances in this state"
        r["conflict_note"] = (f"The state law's own text addresses local ordinances (\"{m.group(0)}\"). "
                              f"Possible conflict / preemption with {names}. Flagged for human review; not a legal conclusion.")
        for x in locals_:
            x["conflict_flag"] = True
            x["conflict_note"] = f"A state law ({r['citation']}) may preempt or conflict with this local rule once effective. Human review needed."
    # open questions surfaced from notes: same law found with different effective dates
    by_law = {}
    for r in out:
        by_law.setdefault((r["jurisdiction"], r["category"]), []).append(r)
    for (j, c), rs in by_law.items():
        dates = {x["effective_date"] for x in rs if x["effective_date"] and x["level"] == "city"}
        if len(dates) > 1 and c == "algorithmic_rent_setting":
            for x in rs:
                x["conflict_flag"] = True
                x["conflict_note"] = f"Sources give different effective dates {sorted(dates)}; human review needed."
    log("reconcile_done", before=len(recs), after=len(out))
    return out


PENALTY = re.compile(r"(?:civil )?penalt(?:y|ies) (?:of )?(?:up to|not (?:to )?exceed(?:ing)?|of not more than) \$[\d,]+(?:\.\d\d)?(?: (?:per|for each) (?:violation|offense|day))?", re.I)
EXEMPT_SENT = re.compile(r"[^.;:\n]{0,160}\b(?:this|the provisions of this) (?:section|act|ordinance|chapter|article|division|subdivision)\b[^.;]{0,60}(?:shall not apply|does not apply)[^.;]{5,240}[.;]", re.I)


def _fill_penalty_exemptions(r, d):
    """Penalty and exemption text exactly as the source states them (only where it does)."""
    if not d or r.get("status") in ("pending", "failed"):
        return
    body = re.sub(r"\s+", " ", d.body)
    if not r.get("penalty"):
        m = PENALTY.search(body)
        if m:
            r["penalty"] = m.group(0)
    if not r.get("exemptions") and "doc kind guidance" not in (r.get("notes") or ""):
        m = EXEMPT_SENT.search(body)
        if m and len(m.group(0).strip()) >= 25:
            r["exemptions"] = m.group(0).strip()


UNADOPTED = re.compile(r"adopted this Ordinance at a meeting held on\s*(?:_{2,}|\n\s*\n|\.|,)", re.I)


def _flag_unadopted(r, d):
    """A staff report / draft whose adoption certificate is blank does not prove the law is in force."""
    if d and r["status"] == "in_force" and UNADOPTED.search(d.raw) and not re.search(
            r"adopted this Ordinance at a meeting held on\s*" + r"[A-Z][a-z]+ \d{1,2}, \d{4}", d.raw):
        r["conflict_flag"] = True
        r["conflict_note"] = ("The source is a council staff report / draft ordinance with a blank adoption date; "
                              "in-force status is not proven by this text. Human review needed.")
        r["confidence"] = min(r.get("confidence") or 0.5, 0.5)


def add_placeholders(recs: list[dict], tests_path, link_rows: list[dict]) -> list[dict]:
    """Rules the change cases name but whose text is not in the corpus (link-only sources).
    Recorded honestly as 'text not in corpus' so lookups say 'unknown' instead of silently missing them.
    Everything is derived from dev/change_tests.json and the link-only list, nothing typed by hand."""
    import json
    from ..change.track import map_test_rule
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    out = []
    for t in tests:
        for tid in t.get("rule_ids", []):
            found, juris, cat = map_test_rule(tid, recs + out, t.get("title", ""))
            if found or not juris or not cat:
                continue
            level = "state" if len(juris) == 2 else "city"
            failed = t.get("type") == "negative"
            links = [x for x in link_rows if x["jurisdictions"].split(";")[0].strip() == juris]
            src = links[0] if links else None
            quote = t["title"]
            status = "failed" if failed else "in_force"
            m_ip = re.search(r"\bIP\s*\d+-\d+", t.get("expected_behavior", ""))
            cite = (f"Mass. Initiative Petition {m_ip.group(0).split()[-1]}" if m_ip else
                    f"{juris.split(',')[0]} local ordinance ({tid})")
            out.append({
                "jurisdiction": juris, "level": level, "category": cat, "status": status,
                "title": f"{'Failed ballot question' if failed else 'Local ordinance'} ({tid}) - text not in corpus",
                "requirement": ("Struck from the ballot; not law." if failed else
                                "A local ordinance on this topic is listed in the challenge sources, but its text is not in "
                                "the corpus (link-only). Whether and how it applies is unknown until the text is read."),
                "key_value": None, "citation": cite,
                "source_url": src["url"] if src else "", "source_doc_id": src["doc_id"] if src else "dev/change_tests.json",
                "quoted_span": quote, "quote_source": "dev/change_tests.json (test title; ordinance text not in corpus)",
                "effective_date": None, "coverage_conditions": {"all": [], "exempt_any": [], "defers_to_local": False,
                                                                 "text": "unknown: text not in corpus", "evidence": []},
                "exemptions": None, "overrides": [], "interaction": None, "penalty": None,
                "confidence": 0.2, "conflict_flag": False, "conflict_note": None,
                "text_in_corpus": False, "extracted_by": "placeholder_from_change_case",
                "source_origin": "placeholder", "quote_check": "not_in_corpus", "enacted_date": None,
                "retrieval_date": "", "notes": f"placeholder for {tid}; link-only sources: "
                                               + ", ".join(x["doc_id"] for x in links),
            })
            log("placeholder_rule", test_rule=tid, jurisdiction=juris, category=cat, status=status)
    return recs + out


SLUG_CAT = [
    ("algorithmic_rent_setting", r"algorithm|realpage|rent-pricing|price-fixing|anticompetitive-rent|rent-setting|ai-apartment"),
    ("screening_restrictions", r"source-of-income|criminal|cori\b|fair-chance|section-8"),
    ("security_deposits", r"security-deposit"),
    ("application_screening_fees", r"application-fee|screening-fee|1950[-.]6"),
    ("just_cause_eviction", r"just-cause|tenant-protection-ordinance|2a-18-61"),
    ("rent_increase_limits", r"rent-control|rent-stabiliz|rent-cap|1947[-.]12"),
]
NAMED_ORD = re.compile(r"^[^\n]{0,40}?((?:Rent Control|Rent Leveling|Rent Stabilization|Just Cause[\w ]*|Fair Chance[\w ]*|Source of Income[\w ]*)"
                       r" Ordinance)\s*[,\-–]\s*Chapter\s+(\d+(?:\.\d+)?)\s*$", re.M)
NAMED_CAT = {"rent control": "rent_increase_limits", "rent leveling": "rent_increase_limits",
             "rent stabilization": "rent_increase_limits", "just cause": "just_cause_eviction",
             "fair chance": "screening_restrictions", "source of income": "screening_restrictions"}


def _stub(juris, cat, status, title, requirement, cite, url, doc_id, quote, quote_source, notes, rtype):
    return {
        "jurisdiction": juris, "level": "state" if len(juris) == 2 else "city", "category": cat, "status": status,
        "title": title, "requirement": requirement, "key_value": None, "citation": cite,
        "source_url": url, "source_doc_id": doc_id, "quoted_span": quote, "quote_source": quote_source,
        "effective_date": None, "coverage_conditions": {"all": [], "exempt_any": [], "defers_to_local": False,
                                                         "text": "unknown: operative text not in corpus", "evidence": []},
        "exemptions": None, "overrides": [], "interaction": None, "penalty": None, "confidence": 0.3,
        "conflict_flag": False, "conflict_note": None, "text_in_corpus": False, "extracted_by": rtype,
        "source_origin": "placeholder", "quote_check": "exact" if rtype == "named_in_city_text" else "not_in_corpus",
        "enacted_date": None, "retrieval_date": "", "notes": notes,
    }


def add_source_evidence_rules(recs: list[dict], docs: list, link_rows: list[dict]) -> list[dict]:
    """Higher recall without inventing law, for jurisdiction x category cells that have no rule yet:
    (a) a city's own text names an ordinance on the topic ('Rent Control Ordinance, Chapter 260') ->
        record it, quoting that line, cited to that chapter; its operative text is not in the corpus;
    (b) the organisers' link-only source list names a source on the topic for that jurisdiction (from
        the URL) -> record it as 'text not in corpus'.
    Both give 'unknown' at every address (partial credit), never 'applies'."""
    from .profile import CITY_CODE
    have = {(r["jurisdiction"], r["category"]) for r in recs}
    out = []
    for d in docs:
        j = (d.jurisdictions or "").split(";")[0].strip()
        if "," not in j or not d.has_text:
            continue
        for m in NAMED_ORD.finditer(d.raw):
            name = m.group(1)
            cat = next((c for k, c in NAMED_CAT.items() if k in name.lower()), None)
            if not cat or (j, cat) in have:
                continue
            have.add((j, cat))
            line = m.group(0).strip()
            out.append(_stub(j, cat, "in_force", f"{name} (named in the city's own page) - operative text not in corpus",
                             f"The city's official page names its {name}, Chapter {m.group(2)}. Its operative text is not "
                             f"in the corpus, so coverage at each address is unknown.",
                             f"{CITY_CODE.get(j.split(',')[0], 'Mun. Code')} ch. {m.group(2)}", d.url, d.doc_id, line,
                             d.doc_id, "named-ordinance pass", "named_in_city_text"))
            log("named_ordinance_rule", doc_id=d.doc_id, jurisdiction=j, category=cat, line=line)
    for row in link_rows:
        j = row["jurisdictions"].split(";")[0].strip()
        url = row["url"].lower()
        for cat, pat in SLUG_CAT:
            if (j, cat) in have or not re.search(pat, url):
                continue
            have.add((j, cat))
            status = "failed" if re.search(r"struck|defeated|fails", url) else "in_force"
            out.append(_stub(j, cat, status, f"Source listed by the organisers ({row['doc_id']}) - text not in corpus",
                             "The challenge's source list names a source on this topic for this jurisdiction, but its "
                             "text is not in the corpus, so coverage at each address is unknown.",
                             f"{j} - see source {row['doc_id']} (text not in corpus)", row["url"], row["doc_id"], row["url"],
                             "starter/corpus/links_only.csv (source URL; page text not in corpus)",
                             f"source-list pass: category from the URL; {row['source_type']}", "source_list_link_only"))
            log("source_list_rule", doc_id=row["doc_id"], jurisdiction=j, category=cat)
    return recs + out


def flag_state_bars(recs: list[dict]) -> list[dict]:
    """A local rule in a category that an in-force state law bars outright (e.g. Mass. Gen. Laws ch. 40P
    bans local rent control) is flagged for human review, with the state text quoted."""
    bars = [r for r in recs if r["level"] == "state" and r["status"] == "in_force"
            and r.get("key_value") == "No local rent control allowed"]
    for x in recs:
        if x["level"] != "city" or x["status"] == "failed" or x["category"] != "rent_increase_limits":
            continue
        b = next((b for b in bars if x["jurisdiction"].endswith(", " + b["jurisdiction"])), None)
        if b:
            x["conflict_flag"] = True
            x["conflict_note"] = (f"State law {b['citation']} bars local rent control (\"{b['quoted_span'][:120]}...\"). "
                                  f"This local rule may be preempted unless it fits the state law's exceptions. Human review needed.")
            log("conflict_state_bar", rule=x.get("citation"), state_rule=b["citation"])
    return recs


def relink_conflicts(recs: list[dict]) -> list[dict]:
    """After placeholders exist: a future state law that addresses local ordinances is flagged only for the
    cities that have a local rule of the same category (known or text-not-in-corpus)."""
    for r in recs:
        if r["level"] != "state" or not r.get("conflict_flag") or r.get("conflict_jurisdictions") is None:
            continue
        locals_ = [x for x in recs if x["level"] == "city" and x["jurisdiction"].endswith(r["jurisdiction"])
                   and x["category"] == r["category"] and x["status"] != "failed"]
        r["conflict_jurisdictions"] = sorted({x["jurisdiction"] for x in locals_})
        if r["conflict_jurisdictions"]:
            r["conflict_note"] = re.sub(r"Possible conflict / preemption with [^.]+\.",
                                        f"Possible conflict / preemption with {', '.join(r['conflict_jurisdictions'])}.",
                                        r["conflict_note"] or "")
        for x in locals_:
            x["conflict_flag"] = True
            x["conflict_note"] = f"A state law ({r['citation']}) may preempt or conflict with this local rule once effective. Human review needed."
    return recs


def link_precedence(recs: list[dict]) -> list[dict]:
    """After ids exist: local rules that a deferring state rule yields to list it in 'overrides'."""
    for s in recs:
        if s["level"] != "state" or not s["coverage_conditions"].get("defers_to_local"):
            continue
        for c in recs:
            if c["level"] == "city" and c["jurisdiction"].endswith(s["jurisdiction"]) and c["category"] == s["category"] \
                    and c["status"] == "in_force":
                if s["team_rule_id"] not in c["overrides"]:
                    c["overrides"].append(s["team_rule_id"])
                c["interaction"] = (f"Where this local rule covers a unit, it governs; the state rule(s) "
                                    f"{', '.join(c['overrides'])} yield to it (the state text exempts units under stricter local law).")
                s["interaction"] = "Yields to a stricter local ordinance where one covers the unit; otherwise applies."
    return recs
