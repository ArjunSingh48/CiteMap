"""Phase 2c tests: coverage conditions + rules.json validity."""
import json
from citemap import config, schema
from citemap.corpus import docs_with_text
from citemap.extract.build import build_rules

RULES = build_rules(write=True)
DOCS = {d.doc_id: d for d in docs_with_text()}


def one(juris, cat, cite_prefix=""):
    rs = [r for r in RULES if r["jurisdiction"] == juris and r["category"] == cat and cite_prefix in r["citation"]]
    assert rs, (juris, cat, cite_prefix)
    return rs[0]


def test_rules_json_written_and_schema_valid():
    data = json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))
    assert len(data["rules"]) >= 30
    for r in data["rules"]:
        assert schema.is_valid(r), (r["team_rule_id"], schema.errors(r))


def test_ids_unique_and_quotes_exact():
    assert len({r["team_rule_id"] for r in RULES}) == len(RULES)
    for r in RULES:
        if r.get("text_in_corpus") is False:
            continue
        assert r["quoted_span"] in DOCS[r["source_doc_id"]].raw


def test_sf_rent_ordinance_cutoff_1979():
    r = one("San Francisco, CA", "rent_increase_limits")
    assert {"fact": "year_built", "op": "<=", "value": "1979-06-13", "basis": "certificate_of_occupancy"} in r["coverage_conditions"]["all"]
    assert r["key_value"] == "1.6%"


def test_la_rso_cutoff_1978():
    rs = [r for r in RULES if r["jurisdiction"] == "Los Angeles, CA" and r["category"] == "rent_increase_limits"]
    assert rs and any(c["value"] == "1978-10-01" for c in rs[0]["coverage_conditions"]["all"])


def test_la_rso_cutoff_also_covers_rso_evictions():
    # the RSO's 1978 cutoff covers its eviction section; the JCO (units NOT under the RSO) does not get it
    rso = [r for r in RULES if r["jurisdiction"] == "Los Angeles, CA" and "151.09" in r["citation"]][0]
    jco = [r for r in RULES if r["jurisdiction"] == "Los Angeles, CA" and r["category"] == "just_cause_eviction"
           and r["source_doc_id"] == "D040"][0]
    assert any(c.get("value") == "1978-10-01" for c in rso["coverage_conditions"]["all"])
    # the JCO covers exactly the units the RSO does not: the inverted cutoff
    assert jco["coverage_conditions"]["all"] == [{"fact": "year_built", "op": ">", "value": "1978-10-01", "basis": "certificate_of_occupancy"}]


def test_berkeley_built_before_1980():
    r = one("Berkeley, CA", "rent_increase_limits")
    # 'built before 1980' + 'Occupancy after June 1980': a 1980 building is undecidable
    assert {"fact": "year_built", "op": "<=", "value": "1980-06-01", "basis": "certificate_of_occupancy"} in r["coverage_conditions"]["all"]


def test_ca_state_rules_defer_to_local_and_15_year_exemption():
    for cat, cite in (("rent_increase_limits", "Cal. Civ. Code § 1947.12"), ("just_cause_eviction", "Cal. Civ. Code § 1946.2")):
        r = one("CA", cat, cite)
        assert r["coverage_conditions"]["defers_to_local"]
        assert [{"fact": "building_age_years", "op": "<", "value": 15}] in r["coverage_conditions"]["exempt_any"]


def test_ca_small_landlord_deposit_exception():
    r = one("CA", "security_deposits", "Cal. Civ. Code § 1950.5")
    assert any(any(c["fact"] == "landlord_unit_count" for c in cl) for cl in r["coverage_conditions"]["exempt_any"])


def test_nj_fee_cap_exempts_one_two_family():
    r = one("NJ", "application_screening_fees", "P.L. 2025, c. 405")
    assert [{"fact": "units", "op": "<=", "value": 2}] in r["coverage_conditions"]["exempt_any"]


def test_every_coverage_has_evidence_text():
    for r in RULES:
        cc = r["coverage_conditions"]
        if cc["all"] or cc["exempt_any"] or cc["defers_to_local"]:
            assert cc["evidence"], r["team_rule_id"]
