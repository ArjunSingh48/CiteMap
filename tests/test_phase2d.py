"""Phase 2d tests: reconciled rule set."""
from citemap.extract.build import build_rules

R = build_rules(write=True)


def find(j, c):
    return [r for r in R if r["jurisdiction"] == j and r["category"] == c]


def test_no_duplicate_rules():
    keys = [(r["jurisdiction"], r["category"], r["citation"]) for r in R]
    assert len(keys) == len(set(keys))


def test_sa_and_nj_noise_removed():
    assert not find("San Diego, CA", "rent_increase_limits"), "San Diego has no rent cap: purpose clause must be dropped"
    assert not [r for r in find("NJ", "rent_increase_limits") if "P.L. 2025, c. 405" in r["citation"]]


def test_la_does_not_restate_state_cap_as_local():
    assert all(r["key_value"] != "5% + CPI, max 10%" for r in find("Los Angeles, CA", "rent_increase_limits"))


def test_fair_act_conflict_flag():
    r = [x for x in find("NJ", "algorithmic_rent_setting") if "P.L. 2026, c. 43" in x["citation"]][0]
    assert r["conflict_flag"] and "human review" in r["conflict_note"].lower()


def test_existing_state_laws_not_flagged():
    assert not any(r["conflict_flag"] for r in R if r["jurisdiction"] == "CA")


def test_local_rent_control_overrides_state_cap():
    state = [r for r in find("CA", "rent_increase_limits") if "Cal. Civ. Code § 1947.12" in r["citation"]][0]
    sf = find("San Francisco, CA", "rent_increase_limits")[0]
    assert state["team_rule_id"] in sf["overrides"] and sf["interaction"]


def test_pending_and_failed_bills_kept_with_status():
    ma = find("MA", "algorithmic_rent_setting")
    assert len(ma) == 2 and all(r["status"] == "pending" for r in ma)
    assert all(r["status"] == "failed" for r in find("Boston, MA", "rent_increase_limits"))


def test_no_rent_cap_rule_for_boston_or_cambridge():
    for j in ("Boston, MA", "Cambridge, MA"):
        assert not [r for r in find(j, "rent_increase_limits") if r["status"] in ("in_force", "not_yet_effective")]
