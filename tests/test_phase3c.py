"""Phase 3c tests: changes.json for T1-T5 + generic new-law diff."""
import json
from citemap import config
from citemap.apply.facts import load_addresses
from citemap.resolve.jurisdiction import resolve
from citemap.change.track import run_tests, diff_rules

C = run_tests(write=True)
CITY = {r["address_id"]: (resolve(r).state, resolve(r).city) for r in load_addresses()}


def ids_where(pred):
    return {a for a, sc in CITY.items() if pred(*sc)}


def test_official_format():
    data = json.loads((config.OUTPUTS / "changes.json").read_text(encoding="utf-8"))
    assert set(data) == {"T1", "T2", "T3", "T4", "T5"}
    for v in data.values():
        assert {"affected_address_ids", "conflict_flag_address_ids", "notes"} <= set(v)


def test_t1_every_ca_address():
    assert set(C["T1"]["affected_address_ids"]) == ids_where(lambda s, c: s == "CA")


def test_t2_hoboken_and_jersey_city_only_never_newark():
    got = set(C["T2"]["affected_address_ids"])
    assert got == ids_where(lambda s, c: c in ("Hoboken", "Jersey City"))
    assert not got & ids_where(lambda s, c: c == "Newark")


def test_t3_all_nj_and_flags_only_jc_hoboken():
    assert set(C["T3"]["affected_address_ids"]) == ids_where(lambda s, c: s == "NJ")
    assert set(C["T3"]["conflict_flag_address_ids"]) == ids_where(lambda s, c: c in ("Hoboken", "Jersey City"))


def test_t4_all_ma_pending():
    assert set(C["T4"]["affected_address_ids"]) == ids_where(lambda s, c: s == "MA")
    assert "never reported as in force" in C["T4"]["notes"]


def test_t5_empty():
    assert C["T5"]["affected_address_ids"] == []


def test_generic_new_law_diff_future_cambridge_ordinance():
    fake = {"team_rule_id": "r-new-1", "jurisdiction": "Cambridge, MA", "level": "city", "category": "security_deposits",
            "status": "not_yet_effective", "effective_date": "2027-03-01", "citation": "Test Ordinance", "overrides": [],
            "coverage_conditions": {"all": [{"fact": "units", "op": ">=", "value": 4}], "exempt_any": [], "defers_to_local": False},
            "conflict_flag": False, "key_value": "1 month's rent"}
    rep = diff_rules([fake], ["2026-10-01", "2027-03-02"])
    cam = ids_where(lambda s, c: c == "Cambridge")
    assert set(rep["2026-10-01"]) <= cam and all(v["r-new-1"] == "not_yet_effective" for v in rep["2026-10-01"].values())
    assert all(v["r-new-1"] == "applies" for v in rep["2027-03-02"].values()) and rep["2027-03-02"]
