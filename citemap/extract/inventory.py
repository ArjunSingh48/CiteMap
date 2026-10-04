"""Rule inventory: every jurisdiction x category, with what we found and why.

outputs/rule_inventory.json is a coverage matrix and a missing-rule detector in one:

  status = rule_found              an extracted (or placeholder) rule exists
           no_local_rule_state_bars  a state law in the corpus bars this kind of local rule (quoted)
           possible_gap            the corpus text for this jurisdiction talks about the topic, but no
                                   rule passed the quality bar; the best candidate sentences are listed
                                   for human review (high-recall pass, never added to rules.json)
           text_not_in_corpus      only link-only sources exist for this jurisdiction
           no_rule_found_in_corpus nothing in the corpus text for this jurisdiction mentions the topic

Negative findings ('no rule at this level') are kept here as structured records with their basis,
because the official rules.json format has no field for them.
"""
from __future__ import annotations
import json
import re

from .. import config
from ..corpus import load_manifest, HEADER_RE
from .heuristic import CAT_TERMS, score_sentence
from .text import paragraphs, sentences


def _juris_of(doc) -> str:
    return (doc.jurisdictions or "").split(";")[0].strip()


def build_inventory(rules: list[dict], as_of: str = config.DEFAULT_AS_OF, write: bool = True) -> dict:
    docs = load_manifest()
    jurisdictions = list(config.STATES) + [f"{c}, {s}" for s, cs in config.CITIES.items() for c in cs]
    bars = [r for r in rules if r["level"] == "state" and r.get("key_value") == "No local rent control allowed"]
    rows = []
    for j in jurisdictions:
        jdocs = [d for d in docs if _juris_of(d) == j]
        text_docs = [d for d in jdocs if d.has_text]
        link_only = [d.doc_id for d in jdocs if not d.has_text]
        for cat in config.CATEGORIES:
            found = [r for r in rules if r["jurisdiction"] == j and r["category"] == cat]
            row = {"jurisdiction": j, "level": "state" if len(j) == 2 else "city", "category": cat,
                   "rule_ids": [r["team_rule_id"] for r in found],
                   "operative_rule_ids": [r["team_rule_id"] for r in found if r.get("record_type") == "operative_law"],
                   "text_docs": [d.doc_id for d in text_docs], "link_only_docs": link_only}
            if found:
                row["status"] = "rule_found"
            else:
                bar = next((b for b in bars if cat == "rent_increase_limits" and j.endswith(", " + b["jurisdiction"])), None)
                cands = []
                for d in text_docs:
                    m = HEADER_RE.match(d.raw)
                    for p in paragraphs(d.raw, m.end() if m else 0):
                        for a, b, s in sentences(p):
                            if 25 <= len(s) <= 600 and re.search(CAT_TERMS[cat][0], s, re.I):
                                sc, _ = score_sentence(s, cat, as_of)
                                if sc >= 4:
                                    cands.append({"doc_id": d.doc_id, "score": sc, "text": s})
                cands.sort(key=lambda x: -x["score"])
                # classify each candidate before calling it a gap
                own_city = j.split(",")[0]
                other_cities = [c for cs in config.CITIES.values() for c in cs if c != own_city]
                kept_c, rejected = [], []
                for c in cands:
                    t = c["text"]
                    if any(re.search(r"\b" + re.escape(oc) + r"\b", t) for oc in other_cities) \
                            and not re.search(r"\b" + re.escape(own_city) + r"\b", t):
                        rejected.append(dict(c, rejection_reason="cross_jurisdiction_reference"))
                    elif re.search(r"public accommodation", t, re.I) and not re.search(r"hous|rent|tenan|landlord", t, re.I):
                        rejected.append(dict(c, rejection_reason="wrong_category (public accommodation, not housing)"))
                    elif cat == "rent_increase_limits" and len(j) == 2 and re.search(
                            r"rent control or rent leveling ordinance|municipal ordinance|local ordinance", t, re.I):
                        rejected.append(dict(c, rejection_reason="state text defers to municipal ordinances (no state numeric cap)"))
                    else:
                        kept_c.append(c)
                cands = kept_c
                if rejected:
                    row["rejected_candidates"] = rejected[:3]
                defer = next((c for c in rejected if c["rejection_reason"].startswith("state text defers")), None)
                if defer:
                    row.update(status="no_rule_found_in_corpus",
                               basis="No state numeric rent cap: the state text defers to municipal rent control / "
                                     "rent leveling ordinances (and an unconscionable-increase standard).",
                               basis_doc=defer["doc_id"], basis_quote=defer["text"])
                elif bar:
                    row.update(status="no_local_rule_state_bars", basis_rule=bar["team_rule_id"],
                               basis_citation=bar["citation"], basis_quote=bar["quoted_span"])
                elif cands:
                    row.update(status="possible_gap", review_candidates=cands[:3],
                               note="The corpus discusses this topic here, but no sentence passed the rule-quality bar. "
                                    "Candidates are listed for human review; they are not reported as law.")
                elif not text_docs and link_only:
                    row["status"] = "text_not_in_corpus"
                else:
                    row.update(status="no_rule_found_in_corpus",
                               basis=(f"No sentence in the {len(text_docs)} text document(s) for {j} states a rule on this topic"
                                      + (" (candidates were rejected; see rejected_candidates)." if rejected else ".")))
            rows.append(row)
    summary = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    out = {"as_of": as_of, "summary": summary, "matrix": rows, "no_rule_findings": no_rule_findings(rows)}
    if write:
        (config.OUTPUTS / "rule_inventory.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def no_rule_findings(rows: list[dict]) -> list[dict]:
    """'No rule at this level' as structured records: only where the corpus text for that jurisdiction was
    searched and holds no rule (or a state law bars the local rule). Link-only jurisdictions are NOT
    reported as 'no rule' (we cannot know), so they never appear here."""
    out = []
    for r in rows:
        if r["status"] not in ("no_rule_found_in_corpus", "no_local_rule_state_bars"):
            continue
        rec = {"finding_id": f"nr-{len(out) + 1:04d}", "finding": "no_rule_at_this_level",
               "jurisdiction": r["jurisdiction"], "level": r["level"], "category": r["category"],
               "searched_doc_ids": r["text_docs"]}
        if r["status"] == "no_local_rule_state_bars":
            rec.update(basis="State law bars a local rule of this kind.", citation=r["basis_citation"],
                       quoted_span=r["basis_quote"], basis_rule_id=r["basis_rule"])
        else:
            rec.update(basis=r["basis"], citation=None, quoted_span=r.get("basis_quote"), basis_doc=r.get("basis_doc"))
        if r.get("rejected_candidates"):
            rec["rejected_candidates"] = r["rejected_candidates"]
        out.append(rec)
    return out
