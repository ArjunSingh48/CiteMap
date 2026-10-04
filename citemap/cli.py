"""CiteMap command line.

  python -m citemap.cli all                     extract -> lookups -> changes (+ T6 if a new law exists)
  python -m citemap.cli add-law FILE --jurisdiction "Cambridge, MA"
                                                 drop a new/changed law in and rerun everything unaided
  python -m citemap.cli lookup A0001 [--as-of 2027-07-02]
  python -m citemap.cli stats
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
from datetime import date, timedelta

from . import config
from .audit import log


def cmd_all(as_of: str) -> None:
    from .extract.build import build_rules
    from .apply.run_lookups import run as run_lookups
    from .change.track import run_tests
    rules = build_rules(as_of)
    print(f"[1/3] rules.json: {len(rules)} rules", file=sys.stderr)
    inv = json.loads((config.OUTPUTS / "rule_inventory.json").read_text(encoding="utf-8"))
    print(f"      rule_inventory.json: {inv['summary']}; no_rule_findings: {len(inv['no_rule_findings'])} (also in rules.json)", file=sys.stderr)
    out = run_lookups(as_of)
    print(f"[2/3] lookups.json: {len(out['lookups'])} addresses", file=sys.stderr)
    ch = run_tests()
    new = new_law_change(rules, as_of)
    if new:
        ch["T6"] = new
        (config.OUTPUTS / "changes.json").write_text(json.dumps(ch, indent=1, ensure_ascii=False), encoding="utf-8")
    publish()
    print(f"[3/3] changes.json: {', '.join(f'{k}={len(v['affected_address_ids'])}' for k, v in ch.items())}", file=sys.stderr)


def publish() -> None:
    """Copy outputs into web/data so the web app also works with no server (static fail-open mode)."""
    d = config.ROOT / "web" / "data"
    d.mkdir(parents=True, exist_ok=True)
    for f in ("rules.json", "lookups_detail.json", "changes.json", "rule_inventory.json"):
        if (config.OUTPUTS / f).exists():
            shutil.copy(config.OUTPUTS / f, d / f)
    shutil.copy(config.CHANGE_TESTS, d / "change_tests.json")
    stale = d / "lookups.json"                      # never used by the app; avoid a stale copy
    if stale.exists():
        stale.unlink()


def new_law_change(rules: list[dict], as_of: str) -> dict | None:
    """T6-style entry for rules extracted from documents in data/new_laws/."""
    from .change.track import diff_rules
    new = [r for r in rules if str(r.get("source_doc_id", "")).startswith("N-")]
    if not new:
        (config.OUTPUTS / "new_law_report.json").unlink(missing_ok=True)   # no stale report
        return None
    dates = [as_of]
    for r in new:
        if r.get("effective_date") and len(r["effective_date"]) == 10:
            dates.append((date.fromisoformat(r["effective_date"]) + timedelta(days=1)).isoformat())
    rep = diff_rules(new, sorted(set(dates)))
    affected = sorted({a for d in rep.values() for a in d})
    flagged = sorted({a for d in rep.values() for a, v in d.items()
                      if any(r["conflict_flag"] for r in new if r["team_rule_id"] in v)})
    (config.OUTPUTS / "new_law_report.json").write_text(json.dumps(
        {"new_rules": [{k: r[k] for k in ("team_rule_id", "jurisdiction", "category", "status", "effective_date",
                                         "citation", "key_value", "quoted_span")} for r in new],
         "results_by_date": rep}, indent=1, ensure_ascii=False), encoding="utf-8")
    eff = ", ".join(sorted({r.get("effective_date") or "no date found" for r in new}))
    log("new_law_tracked", rules=len(new), affected=len(affected))
    return {"affected_address_ids": affected, "conflict_flag_address_ids": flagged,
            "notes": f"New law(s) from data/new_laws extracted automatically: {', '.join(r['citation'] for r in new)}; "
                     f"effective {eff}; results compared on {', '.join(sorted(set(dates)))}"}


def cmd_add_law(path: str, jurisdiction: str, source_url: str | None, as_of: str) -> None:
    config.NEW_LAWS_DIR.mkdir(parents=True, exist_ok=True)
    src = open(path, encoding="utf-8", errors="replace").read()
    head = ""
    if not src.startswith("SOURCE:"):
        head += f"SOURCE: {source_url or 'file://' + Path(path).name}\nRETRIEVED: {date.today().isoformat()} 00:00 UTC\n"
    if "JURISDICTION:" not in src[:500]:
        head += f"JURISDICTION: {jurisdiction}\n"
    dest = config.NEW_LAWS_DIR / (Path(path).stem + ".txt")      # works with / and \\ paths
    dest.write_text(head + ("\n" if head else "") + src, encoding="utf-8")
    log("new_law_added", file=str(dest), jurisdiction=jurisdiction)
    print(f"added {dest}", file=sys.stderr)
    cmd_all(as_of)
    rep = config.OUTPUTS / "new_law_report.json"
    if rep.exists():
        r = json.loads(rep.read_text(encoding="utf-8"))
        print(json.dumps(r["new_rules"], indent=1, ensure_ascii=False)[:3000])
        for d, v in r["results_by_date"].items():
            print(f"as of {d}: {len(v)} addresses affected", file=sys.stderr)
    else:
        print("WARNING: no rule could be extracted from the new document (check category words / text)", file=sys.stderr)


def _check_date(as_of: str) -> str:
    try:
        if len(as_of) != 10 or not as_of.isascii():
            raise ValueError
        date.fromisoformat(as_of)
        return as_of
    except ValueError:
        sys.exit(f"--as-of must be a real date like 2026-10-01 (got {as_of!r})")


def cmd_lookup(aid: str, as_of: str) -> None:
    as_of = _check_date(as_of)
    from .apply.facts import load_addresses
    from .apply.run_lookups import lookup_one, load_rules
    row = next((r for r in load_addresses() if r["address_id"] == aid), None)
    if not row:
        sys.exit(f"unknown address id {aid}")
    res = lookup_one(row, load_rules(), as_of)
    print(json.dumps(res, indent=1, ensure_ascii=False))


def cmd_stats() -> None:
    rules = json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))["rules"]
    lk = json.loads((config.OUTPUTS / "lookups.json").read_text(encoding="utf-8"))
    from collections import Counter
    c = Counter(it["result"] for v in lk["lookups"].values() for it in v)
    print(json.dumps({"rules": len(rules), "by_status": Counter(r["status"] for r in rules),
                      "by_category": Counter(r["category"] for r in rules), "lookup_results": c,
                      "addresses": len(lk["lookups"])}, indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(prog="citemap")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("all"); a.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    n = sub.add_parser("add-law"); n.add_argument("file"); n.add_argument("--jurisdiction", required=True)
    n.add_argument("--source-url"); n.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    l = sub.add_parser("lookup"); l.add_argument("address_id"); l.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    sub.add_parser("stats")
    sub.add_parser("clean-new-laws")
    args = p.parse_args(argv)
    if args.cmd == "all":
        cmd_all(args.as_of)
    elif args.cmd == "add-law":
        cmd_add_law(args.file, args.jurisdiction, args.source_url, args.as_of)
    elif args.cmd == "lookup":
        cmd_lookup(args.address_id, args.as_of)
    elif args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "clean-new-laws":
        shutil.rmtree(config.NEW_LAWS_DIR, ignore_errors=True)
        print("removed data/new_laws", file=sys.stderr)


if __name__ == "__main__":
    main()
