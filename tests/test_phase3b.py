"""Phase 3b tests: lookups.json in the official format + explanations."""
import json
from citemap import config
from citemap.apply.run_lookups import run

OUT = run(write=True)
DET = json.loads((config.OUTPUTS / "lookups_detail.json").read_text(encoding="utf-8"))["lookups"]
RULES = {r["team_rule_id"]: r for r in json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))["rules"]}
VALID = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}


def test_official_format_all_500():
    assert OUT["as_of"] == "2026-10-01"
    assert len(OUT["lookups"]) == 500
    for aid, items in OUT["lookups"].items():
        for it in items:
            assert set(it) == {"team_rule_id", "result", "explanation", "conflict_flag"}
            assert it["result"] in VALID and it["team_rule_id"] in RULES
            assert it["explanation"] and isinstance(it["conflict_flag"], bool)


def test_every_explanation_cites_its_source():
    for items in OUT["lookups"].values():
        for it in items:
            r = RULES[it["team_rule_id"]]
            assert r["citation"] in it["explanation"] or (r.get("text_in_corpus") is False and r["source_url"] in it["explanation"])


def test_unknown_names_the_missing_fact():
    for v in DET.values():
        for it in v["results"]:
            if it["result"] == "unknown" and "not in our corpus" not in it["explanation"]:
                assert "depends on" in it["explanation"] and "public records" in it["explanation"]


def test_spanish_present():
    any_es = [it["explanation_es"] for v in DET.values() for it in v["results"]]
    assert all(any_es) and any("Fuente" in x or "propuesta" in x for x in any_es)


def test_no_failed_rules_and_no_boston_cambridge_rent_cap():
    for v in DET.values():
        for it in v["results"]:
            r = RULES[it["team_rule_id"]]
            assert r["status"] != "failed"
            if v["jurisdiction"]["city"] in ("Boston", "Cambridge") and r["category"] == "rent_increase_limits":
                assert r["key_value"] == "No local rent control allowed"


def test_newark_never_gets_city_rules_from_other_cities():
    for v in DET.values():
        if v["jurisdiction"]["city"] == "Newark":
            for it in v["results"]:
                assert RULES[it["team_rule_id"]]["jurisdiction"] in ("NJ", "Newark, NJ")
