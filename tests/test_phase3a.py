"""Phase 3a tests: the rules engine on real rules + controlled building facts."""
import json
from citemap import config
from citemap.apply.engine import decide_address, eval_cond, and3, or3
from citemap.apply.facts import Facts
from citemap.resolve.jurisdiction import Jurisdiction

RULES = json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))["rules"]
AS_OF = "2026-10-01"


def rid(j, cat, cite_start=""):
    return [r["team_rule_id"] for r in RULES if r["jurisdiction"] == j and r["category"] == cat and cite_start in r["citation"]][0]


def run(city, state, yb, umin, umax=None, as_of=AS_OF):
    f = Facts(yb, umin, umax if umax is not None else umin, "test", "", "")
    j = Jurisdiction(state, None, city, "test", 1.0)
    return {d.team_rule_id: d for d in decide_address(RULES, f, j, as_of)}


def test_three_valued_logic():
    assert and3([True, None]) is None and and3([True, False, None]) is False
    assert or3([False, None]) is None and or3([None, True]) is True


def test_sf_1962_20_units_rent_ordinance_governs():
    d = run("San Francisco", "CA", 1962, 20)
    assert d[rid("San Francisco, CA", "rent_increase_limits")].result == "applies"
    assert d[rid("CA", "rent_increase_limits", "Cal. Civ. Code § 1947.12")].result == "superseded"


def test_sf_built_in_cutoff_year_is_unknown():
    d = run("San Francisco", "CA", 1979, 10)
    r = d[rid("San Francisco, CA", "rent_increase_limits")]
    assert r.result == "unknown" and "certificate_of_occupancy_date" in r.missing_facts


def test_sf_new_building_state_cap_applies():
    d = run("San Francisco", "CA", 1990, 10)
    assert rid("San Francisco, CA", "rent_increase_limits") not in d
    assert d[rid("CA", "rent_increase_limits", "Cal. Civ. Code § 1947.12")].result == "applies"


def test_ca_15_year_exemption():
    d = run("San Francisco", "CA", 2020, 10)
    assert rid("CA", "rent_increase_limits", "Cal. Civ. Code § 1947.12") not in d   # exempt: < 15 years old


def test_missing_year_gives_unknown_not_omission():
    d = run("San Diego", "CA", None, 5, None)
    r = d[rid("CA", "rent_increase_limits", "Cal. Civ. Code § 1947.12")]
    assert r.result == "unknown" and "year_built" in r.missing_facts


def test_small_landlord_exception_ruled_out_for_big_buildings():
    d = run("Los Angeles", "CA", 1970, 32)
    assert d[rid("CA", "security_deposits", "Cal. Civ. Code § 1950.5")].result == "applies"


def test_t1_ab325_dates():
    r = rid("CA", "algorithmic_rent_setting")
    assert run("Los Angeles", "CA", 1970, 10, as_of="2025-12-31")[r].result == "not_yet_effective"
    assert run("Los Angeles", "CA", 1970, 10, as_of="2026-01-02")[r].result == "applies"


def test_t3_fair_act_dates_and_flag():
    r = rid("NJ", "algorithmic_rent_setting", "P.L. 2026, c. 43")
    now = run("Newark", "NJ", None, 5, None)[r]
    later = run("Newark", "NJ", None, 5, None, as_of="2027-07-02")[r]
    assert now.result == "not_yet_effective" and later.result == "applies"
    assert not now.conflict_flag                                    # Newark has no local ban: no conflict flag
    assert run("Jersey City", "NJ", None, 5, None)[r].conflict_flag  # Jersey City / Hoboken: flagged for review


def test_t4_t5_massachusetts():
    d = run("Boston", "MA", 1920, 7, 30)
    pend = [x for x in d.values() if x.result == "pending"]
    assert len(pend) == 2
    rent_now = [x for x in d.values() if x.team_rule_id in
                {r["team_rule_id"] for r in RULES if r["category"] == "rent_increase_limits" and r["key_value"] != "No local rent control allowed"}
                and x.result in ("applies", "unknown")]
    assert not rent_now


def test_nj_fee_exemption_units_range():
    f = eval_cond({"fact": "units", "op": "<=", "value": 2}, Facts(None, 5, None, "", "", ""), AS_OF)
    assert f == (False, None)
    u = eval_cond({"fact": "units", "op": "<=", "value": 2}, Facts(None, None, None, "", "", ""), AS_OF)
    assert u[0] is None
