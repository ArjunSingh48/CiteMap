"""Module A driver: corpus -> candidate rules -> coverage -> ids -> schema check -> outputs/rules.json."""
from __future__ import annotations
import json
import sys

from .. import config, schema
from ..audit import log
from ..corpus import docs_with_text
from . import coverage
from .structure import extract_all


def build_rules(as_of: str = config.DEFAULT_AS_OF, write: bool = True) -> list[dict]:
    docs = docs_with_text()
    recs = extract_all(docs, as_of)
    findings = [f for d in docs for f in coverage.find(d)]
    recs = coverage.attach(recs, findings, {d.doc_id: d.raw for d in docs})
    from .reconcile import reconcile, add_placeholders
    recs = reconcile(recs, as_of)
    import csv
    with open(config.STARTER / "corpus" / "links_only.csv", newline="", encoding="utf-8") as f:
        link_rows = list(csv.DictReader(f))
    from .reconcile import add_source_evidence_rules
    from ..corpus import load_manifest
    recs = add_source_evidence_rules(recs, load_manifest(), link_rows)
    recs = add_placeholders(recs, config.CHANGE_TESTS, link_rows)
    from .reconcile import relink_conflicts, flag_state_bars
    recs = relink_conflicts(recs)
    recs = flag_state_bars(recs)
    # placeholders and new laws (data/new_laws) sort LAST so existing rule ids never shift when a law is added
    recs.sort(key=lambda r: ({"new_law": 2, "placeholder": 1}.get(r.get("source_origin"), 0), r["jurisdiction"], r["category"], r["citation"]))
    bad = []
    for i, r in enumerate(recs, 1):
        r["team_rule_id"] = f"r-{i:04d}"
    from .reconcile import link_precedence
    recs = link_precedence(recs)
    # keep operative law apart from legislative history, and say how authoritative each source is
    from ..corpus import load_manifest
    stype = {d.doc_id: (d.source_type or "") for d in load_manifest()}
    for r in recs:
        r["record_type"] = ("named_ordinance_text_not_in_corpus" if r.get("extracted_by") == "named_in_city_text" else
                            "placeholder_text_not_in_corpus" if r.get("text_in_corpus") is False else
                            "failed_proposal" if r["status"] == "failed" else
                            "pending_bill" if r["status"] == "pending" else "operative_law")
        t = stype.get(r["source_doc_id"], "").lower()
        r["source_authority"] = ("official" if t.startswith("official") else "code publisher" if "code" in t
                                 else "secondary (news / law firm)" if t else "unknown")
        r["extraction_score_note"] = "confidence = heuristic extraction score, not a calibrated probability"
    for r in recs:
        errs = schema.errors(r)
        if errs:
            bad.append((r["team_rule_id"], errs))
    if bad:
        log("schema_errors", errors=bad[:20])
        raise SystemExit(f"{len(bad)} rule records fail the official schema: {bad[:3]}")
    if write:
        config.OUTPUTS.mkdir(exist_ok=True)
        from .inventory import build_inventory
        inv = build_inventory(recs, as_of, write=True)
        # 'no rule at this level' findings ride along under their own key; the "rules" list keeps the template format
        txt = json.dumps({"rules": recs, "no_rule_findings": inv["no_rule_findings"]}, indent=2, ensure_ascii=False)
        (config.OUTPUTS / "rules.json").write_text(txt, encoding="utf-8")
        import hashlib
        log("rules_written", count=len(recs), findings=len(findings),
            sha256=hashlib.sha256(txt.encode("utf-8")).hexdigest(),
            corpus={d.doc_id: d.sha256[:16] for d in docs})
    return recs


if __name__ == "__main__":
    rs = build_rules()
    print(f"wrote {len(rs)} rules to outputs/rules.json", file=sys.stderr)
