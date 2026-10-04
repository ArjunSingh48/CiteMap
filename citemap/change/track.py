"""Phase 3c: Module C, change tracking.

* run_tests(): the supplied change cases (dev/change_tests.json) -> outputs/changes.json
  in the official format {test_id: {affected_address_ids, conflict_flag_address_ids, notes}}.
* diff_rules(): generic 'what does this new / changed law do?' for ANY new rule set
  (used for the hour-16 ordinance): affected addresses with before/after results.

Test rule ids from the answer key (e.g. 'CA-ALG-01', 'HOB-ALG-01', 'MA-ALG-P1') are mapped to
our own extracted rules by jurisdiction + category (+ pending order), never by hand.
"""
from __future__ import annotations
import json
import re
import sys

from .. import config
from ..audit import log
from ..apply.engine import decide_address
from ..apply.facts import load_addresses, facts_for
from ..resolve.jurisdiction import resolve

PREFIX = {"CA": "CA", "NJ": "NJ", "MA": "MA", "HOB": "Hoboken, NJ", "JC": "Jersey City, NJ", "NWK": "Newark, NJ",
          "SF": "San Francisco, CA", "LA": "Los Angeles, CA", "SD": "San Diego, CA", "BER": "Berkeley, CA",
          "SA": "Santa Ana, CA", "BOS": "Boston, MA", "CAM": "Cambridge, MA"}
CAT = {"ALG": "algorithmic_rent_setting", "RENT": "rent_increase_limits", "DEP": "security_deposits",
       "JCE": "just_cause_eviction", "EVI": "just_cause_eviction", "FEE": "application_screening_fees",
       "SCR": "screening_restrictions"}


def _load():
    rules = json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))["rules"]
    rows = load_addresses()
    ctx = [(r, facts_for(r), resolve(r)) for r in rows]
    return rules, ctx


def map_test_rule(tid: str, rules: list[dict], title: str = "") -> tuple[list[dict], str, str]:
    """('HOB-ALG-01') -> (our matching rules, jurisdiction, category)."""
    m = re.match(r"([A-Z]+)-([A-Z]+)-(P?)(\d+)", tid)
    if not m:
        return [], "", ""
    juris, cat = PREFIX.get(m.group(1), ""), CAT.get(m.group(2), "")
    pend = m.group(3) == "P"
    cands = [r for r in rules if r["jurisdiction"] == juris and r["category"] == cat]
    if pend:
        cands = [r for r in cands if r["status"] in ("pending", "failed")]
        def pos(r):
            num = re.search(r"([SH])\.(\d+)", r["citation"])
            i = title.find(f"{num.group(1)}.{num.group(2)}") if num else -1
            return (i if i >= 0 else 10 ** 6, r["citation"])
        cands.sort(key=pos)
        idx = int(m.group(4)) - 1
        cands = [cands[idx]] if idx < len(cands) else []
    else:
        cands = [r for r in cands if r["status"] not in ("pending", "failed")]
    return cands, juris, cat


def results_for(rules, ctx, as_of, rule_ids: set) -> dict:
    out = {}
    for row, f, j in ctx:
        ds = decide_address(rules, f, j, as_of)
        out[row["address_id"]] = {d.team_rule_id: d.result for d in ds if d.team_rule_id in rule_ids}
    return out


def conflict_for(rules, ctx, as_of, rule_ids: set) -> dict:
    out = {}
    for row, f, j in ctx:
        out[row["address_id"]] = any(d.conflict_flag for d in decide_address(rules, f, j, as_of) if d.team_rule_id in rule_ids)
    return out


def in_scope(ctx, juris: str) -> list[str]:
    ids = []
    for row, f, j in ctx:
        if juris == j.state or juris == f"{j.city}, {j.state}":
            ids.append(row["address_id"])
    return ids


def run_tests(write: bool = True) -> dict:
    rules, ctx = _load()
    tests = json.loads(config.CHANGE_TESTS.read_text(encoding="utf-8"))
    out = {}
    for t in tests:
        tid = t["test_id"]
        mapped, notes, missing_scopes = [], [], []
        for x in t["rule_ids"]:
            rs, juris, cat = map_test_rule(x, rules, t.get("title", ""))
            if rs:
                mapped += rs
                notes.append(f"{x} -> {', '.join(r['team_rule_id'] + ' (' + r['citation'] + ')' for r in rs)}")
            elif t["type"] == "negative":
                notes.append(f"{x}: struck / failed measure; no in-force text in the corpus, so it is never applied")
            else:
                # never borrow a scope from the test case: an unmatched rule simply affects nothing
                notes.append(f"{x}: no matching rule in our rule set, so no addresses are reported for it")
        ids = {r["team_rule_id"] for r in mapped}
        affected, conflicts = set(), set()
        typ = t["type"]
        if typ == "as_of":
            before = results_for(rules, ctx, t["as_of_before"], ids)
            after = results_for(rules, ctx, t["as_of_after"], ids)
            for aid in after:
                if before.get(aid) != after[aid] and any(v == "applies" for v in after[aid].values()):
                    affected.add(aid)
            # conflict flags come from our own engine's answers (rule text), not from the test case
            flags = conflict_for(rules, ctx, t["as_of_before"], ids)
            conflicts |= {a for a in affected if flags.get(a)}
            notes.append(f"{t['as_of_before']}: not yet effective; {t['as_of_after']}: applies "
                         f"({len(affected)} addresses change)")
            if conflicts:
                notes.append(f"{len(conflicts)} addresses flagged by the engine: possible conflict between the state law and "
                             f"local ordinances (from the state law's own preemption language); human review, not a legal conclusion")
        elif typ == "boundary":
            now = results_for(rules, ctx, t["as_of"], ids)
            # in scope = the rule applies, or applies-unless-a-fact-says-otherwise ('unknown', e.g. text not in corpus)
            affected = {a for a, v in now.items() if any(x in ("applies", "unknown") for x in v.values())}
        elif typ == "pending":
            now = results_for(rules, ctx, t["as_of"], ids)
            affected = {a for a, v in now.items() if any(x == "pending" for x in v.values())}
            notes.append(f"Pending bills, never reported as in force; {len(affected)} addresses would be affected if enacted")
        elif typ == "negative":
            caps = {r["team_rule_id"] for r in rules if r["category"] == "rent_increase_limits"
                    and r["jurisdiction"] in [s for s in t.get("states", [])] + [f"{c}, {s}" for s in t.get("states", []) for c in config.CITIES[s]]
                    and r["status"] in ("in_force", "not_yet_effective") and r["key_value"] != "No local rent control allowed"}
            now = results_for(rules, ctx, t["as_of"], caps)
            affected = {a for a, v in now.items() if any(x in ("applies", "unknown") for x in v.values())}
            notes.append("Ballot question struck before the vote: no rent cap is reported for any Boston or Cambridge address"
                         if not affected else "WARNING: a rent cap was found for MA addresses")
        out[tid] = {"affected_address_ids": sorted(affected), "conflict_flag_address_ids": sorted(conflicts),
                    "notes": "; ".join(notes)}
    if write:
        (config.OUTPUTS / "changes.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        log("changes_written", tests=list(out), sizes={k: len(v["affected_address_ids"]) for k, v in out.items()})
    return out


def diff_rules(new_rules: list[dict], as_of_dates: list[str]) -> dict:
    """Generic impact of NEW rules (e.g. the hour-16 ordinance) on all addresses, for each date."""
    rules, ctx = _load()
    ids = {r["team_rule_id"] for r in new_rules}
    allr = rules + [r for r in new_rules if r["team_rule_id"] not in {x["team_rule_id"] for x in rules}]
    report = {}
    for d in as_of_dates:
        res = results_for(allr, ctx, d, ids)
        report[d] = {a: v for a, v in res.items() if v}
    return report


if __name__ == "__main__":
    o = run_tests()
    for k, v in o.items():
        print(k, len(v["affected_address_ids"]), "affected,", len(v["conflict_flag_address_ids"]), "flagged", file=sys.stderr)
