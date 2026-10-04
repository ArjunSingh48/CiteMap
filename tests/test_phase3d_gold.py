"""Phase 3d: gold checks across ALL outputs + the hour-16 style rerun, end to end.

These encode what the brief and participant guide say a correct system must do.
"""
import json
import shutil
from citemap import config
from citemap.corpus import load_manifest
from citemap.apply.facts import load_addresses
from citemap.resolve.jurisdiction import resolve
from citemap import cli

FIX = config.ROOT / "tests" / "fixtures" / "cambridge_test_ordinance.txt"


def load(name):
    return json.loads((config.OUTPUTS / name).read_text(encoding="utf-8"))


import tempfile
_BACKUP = {}


def setup_module():
    """Never destroy real work: back up data/new_laws (e.g. the hour-16 ordinance) and outputs/,
    run the checks on a clean build, then restore both exactly in teardown_module."""
    tmp = tempfile.mkdtemp()
    if config.NEW_LAWS_DIR.exists():
        _BACKUP["laws"] = shutil.copytree(config.NEW_LAWS_DIR, f"{tmp}/new_laws")
    if config.OUTPUTS.exists():
        _BACKUP["out"] = shutil.copytree(config.OUTPUTS, f"{tmp}/outputs")
    shutil.rmtree(config.NEW_LAWS_DIR, ignore_errors=True)
    cli.cmd_all(config.DEFAULT_AS_OF)


def teardown_module():
    shutil.rmtree(config.NEW_LAWS_DIR, ignore_errors=True)
    if "laws" in _BACKUP:
        shutil.copytree(_BACKUP["laws"], config.NEW_LAWS_DIR)
    if "out" in _BACKUP:
        shutil.rmtree(config.OUTPUTS, ignore_errors=True)
        shutil.copytree(_BACKUP["out"], config.OUTPUTS)
        cli.publish()


def test_cross_file_consistency():
    rules = {r["team_rule_id"]: r for r in load("rules.json")["rules"]}
    lk = load("lookups.json")
    ch = load("changes.json")
    addr = {r["address_id"] for r in load_addresses()}
    assert set(lk["lookups"]) == addr
    for items in lk["lookups"].values():
        assert all(it["team_rule_id"] in rules for it in items)
    for v in ch.values():
        assert set(v["affected_address_ids"]) <= addr and set(v["conflict_flag_address_ids"]) <= set(v["affected_address_ids"])


def test_every_rule_has_citation_url_retrieval_and_exact_quote():
    docs = {d.doc_id: d for d in load_manifest()}
    for r in load("rules.json")["rules"]:
        if r.get("text_in_corpus") is False:      # placeholder: link-only source, labelled as such
            assert r["source_url"].startswith("http") and "not in corpus" in r["title"]
            continue
        assert r["citation"] and r["source_url"].startswith(("http", "file")) and r["retrieval_date"]
        assert r["quoted_span"] in docs[r["source_doc_id"]].raw


def test_enacted_vs_pending_separated():
    for r in load("rules.json")["rules"]:
        if r["citation"].startswith("Mass. S.") or r["citation"].startswith("Mass. H.5222"):
            assert r["status"] == "pending"


def test_guide_example_sf_1962_20_units():
    lk = load("lookups.json")["lookups"]
    rules = {r["team_rule_id"]: r for r in load("rules.json")["rules"]}
    for row in load_addresses():
        j = resolve(row)
        if j.city == "San Francisco" and row["year_built"] and int(row["year_built"]) < 1979:
            res = {rules[i["team_rule_id"]]["citation"]: i["result"] for i in lk[row["address_id"]]}
            assert res.get("Cal. Civ. Code § 1947.12") == "superseded"
            assert any(c.startswith("S.F. Admin. Code") and v == "applies" for c, v in res.items())
            return
    raise AssertionError("no pre-1979 SF address found")


def test_hour16_style_new_law_unaided():
    try:
        cli.cmd_add_law(str(FIX), "Cambridge, MA", None, config.DEFAULT_AS_OF)
        ch = load("changes.json")
        assert "T6" in ch and len(ch["T6"]["affected_address_ids"]) == 50
        new = [r for r in load("rules.json")["rules"] if r["source_doc_id"].startswith("N-")]
        assert new and new[0]["effective_date"] == "2027-03-01" and new[0]["status"] == "not_yet_effective"
        assert new[0]["key_value"] == "$25"
    finally:
        shutil.rmtree(config.NEW_LAWS_DIR, ignore_errors=True)
        cli.cmd_all(config.DEFAULT_AS_OF)
    assert "T6" not in load("changes.json")      # real new laws are restored in teardown_module
