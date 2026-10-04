"""Phase 3b: Module B output. Every address -> every rule result, in the official format.

outputs/lookups.json         official submission format (as_of + lookups{address_id: [...]})
outputs/lookups_detail.json  same answers plus jurisdiction stack, facts and Spanish text (for the UI)
"""
from __future__ import annotations
import json
import sys

from .. import config
from ..audit import log
from .engine import decide_address
from .explain import explain, _cond_text
from ..extract.verify import status_as_of
from .facts import load_addresses, facts_for
from ..resolve.jurisdiction import resolve

ORDER = {"applies": 0, "unknown": 1, "superseded": 2, "not_yet_effective": 3, "pending": 4}


def load_rules() -> list[dict]:
    return json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))["rules"]


def lookup_one(row: dict, rules: list[dict], as_of: str, by_id: dict | None = None) -> dict:
    by_id = by_id or {r["team_rule_id"]: r for r in rules}
    f, j = facts_for(row), resolve(row)
    ds = sorted(decide_address(rules, f, j, as_of), key=lambda d: (ORDER[d.result], d.team_rule_id))
    items = []
    for d in ds:
        r = by_id[d.team_rule_id]
        items.append({
            "team_rule_id": d.team_rule_id,
            "result": d.result,
            "explanation": explain(r, d, f, j, by_id, "en"),
            "conflict_flag": d.conflict_flag,
            "explanation_es": explain(r, d, f, j, by_id, "es"),
            "missing_facts": d.missing_facts,
            "superseded_by": d.superseded_by,
            "certainty": ("decided from public facts" if d.result in ("applies", "superseded") else
                          "depends on a missing fact" if d.result == "unknown" else "not in force on this date"),
            "trace": _trace(r, d, j, as_of, by_id),
            "unknown_reason": _unknown_reason(d) if d.result == "unknown" else None,
        })
    return {"address": row, "jurisdiction": {"state": j.state, "county": j.county, "city": j.city,
                                            "method": j.method, "confidence": j.confidence, "note": j.note},
            "facts": f.to_dict(), "results": items}


def _unknown_reason(d) -> str:
    m = set(d.missing_facts)
    if "local_ordinance_text" in m:
        return "legal_text_unavailable"
    if "exact_effective_date" in m:
        return "effective_date_unresolved"
    if any(isinstance(x, tuple) and x and x[0] == "depends_on_local_rule" for x in d.reasons):
        return "depends_on_local_rule_coverage"
    return "missing_public_fact" if m else "rule_scope_unresolved"


def _trace(r, d, j, as_of, by_id) -> list[str]:
    """The decision path, step by step: jurisdiction -> rule -> status on the date -> each coverage
    condition (true / false / unknown) -> missing facts -> precedence -> final answer."""
    tv = {True: "true", False: "false", None: "unknown"}
    out = [f"Jurisdiction: {(j.city + ', ') if j.city else ''}{j.state} (resolved by {j.method})",
           f"Rule: {r['citation']} ({r['level']} {r.get('record_type', 'law')}), source {r.get('source_doc_id')}",
           f"Status on {as_of}: {status_as_of(r['status'], r.get('effective_date'), as_of)}"
           + (f" (effective {r['effective_date']})" if r.get("effective_date") else "")]
    conds = [(c, v) for c, v in d.reasons if isinstance(c, dict)]
    for c, v in conds:
        out.append(f"Condition: {_cond_text(c, 'en')} -> {tv.get(v, v)}")
    if not conds:
        out.append("Conditions: none recorded in the source for this rule")
    if r.get("coverage_conditions", {}).get("exempt_any"):
        out.append(f"Exemptions checked: {len(r['coverage_conditions']['exempt_any'])}")
    if d.missing_facts:
        out.append("Missing facts: " + ", ".join(d.missing_facts))
    for sid in d.superseded_by:
        out.append(f"Precedence: the local rule {by_id[sid]['citation']} governs (the state text yields to it)")
    if any(isinstance(x, tuple) and x and x[0] == "depends_on_local_rule" for x in d.reasons):
        out.append("Precedence: depends on whether the local rule covers this building")
    out.append(f"Answer: {d.result}" + (" (conflict flag: human review)" if d.conflict_flag else ""))
    return out


def run(as_of: str = config.DEFAULT_AS_OF, write: bool = True) -> dict:
    rules = load_rules()
    by_id = {r["team_rule_id"]: r for r in rules}
    official, detail = {}, {}
    for row in load_addresses():
        res = lookup_one(row, rules, as_of, by_id)
        aid = row["address_id"]
        official[aid] = [{k: it[k] for k in ("team_rule_id", "result", "explanation", "conflict_flag")} for it in res["results"]]
        detail[aid] = res
    out = {"as_of": as_of, "lookups": official}
    if write:
        (config.OUTPUTS / "lookups.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        (config.OUTPUTS / "lookups_detail.json").write_text(json.dumps({"as_of": as_of, "lookups": detail}, ensure_ascii=False), encoding="utf-8")
        log("lookups_written", addresses=len(official), as_of=as_of)
    return out


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else config.DEFAULT_AS_OF
    o = run(a)
    print(f"wrote lookups for {len(o['lookups'])} addresses as of {a}", file=sys.stderr)
