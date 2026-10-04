"""Extraction-accuracy regression tests: rules the brief lists as examples, with the right
jurisdiction + category + citation + key value + date (the fields the scorer matches on)."""
from citemap.extract.build import build_rules

R = build_rules(write=True)


def has(j, cat, cite, kv=None):
    # codified citations carry the act in brackets, e.g. 'Cal. Bus. & Prof. Code § 16729 (Cal. AB 325 (2025))'
    rs = [r for r in R if r["jurisdiction"] == j and r["category"] == cat and cite in r["citation"]]
    assert rs, f"missing {j} / {cat} / {cite}; have {[r['citation'] for r in R if r['jurisdiction'] == j and r['category'] == cat]}"
    if kv is not None:
        assert rs[0]["key_value"] == kv, rs[0]["key_value"]
    return rs[0]


def test_brief_example_rules_present_with_values():
    has("CA", "rent_increase_limits", "Cal. Civ. Code § 1947.12", "5% + CPI, max 10%")
    has("CA", "just_cause_eviction", "Cal. Civ. Code § 1946.2")
    has("CA", "security_deposits", "Cal. Civ. Code § 1950.5", "1 month's rent")
    has("CA", "application_screening_fees", "Cal. Civ. Code § 1950.6", "$30, CPI-adjusted annually")
    has("CA", "screening_restrictions", "Cal. Gov. Code § 12955")
    has("CA", "algorithmic_rent_setting", "Cal. Bus. & Prof. Code § 16729 (Cal. AB 325 (2025))")
    has("NJ", "just_cause_eviction", "N.J.S.A. 2A:18-61.1")
    has("NJ", "security_deposits", "N.J.S.A. 46:8-21.2", "1.5 months' rent")
    has("NJ", "application_screening_fees", "N.J.S.A. 46:8-18.1 (P.L. 2025, c. 405)", "$50")
    has("NJ", "screening_restrictions", "P.L. 2021, c. 110")
    has("NJ", "algorithmic_rent_setting", "P.L. 2026, c. 43")
    has("MA", "security_deposits", "Mass. Gen. Laws ch. 186, § 15B", "1 month's rent")
    has("MA", "application_screening_fees", "Mass. Gen. Laws ch. 186, § 15B")
    assert [r for r in R if "87DDD" in r["citation"]]              # corpus statute: fee only via a licensed broker
    assert "last month's rent" in has("MA", "application_screening_fees", "Mass. Gen. Laws ch. 186, § 15B")["key_value"]
    has("MA", "rent_increase_limits", "Mass. Gen. Laws ch. 40P, § 4", "No local rent control allowed")
    has("San Francisco, CA", "rent_increase_limits", "S.F. Admin. Code")
    has("San Francisco, CA", "just_cause_eviction", "S.F. Admin. Code § 37.9")
    has("San Francisco, CA", "algorithmic_rent_setting", "S.F. Admin. Code § 37.10C")
    has("San Diego, CA", "algorithmic_rent_setting", "San Diego Mun. Code § 98.1103")
    has("Berkeley, CA", "algorithmic_rent_setting", "Berkeley Mun. Code § 13.63.030")


def test_dates_and_statuses():
    assert has("CA", "algorithmic_rent_setting", "Cal. AB 325 (2025)")["effective_date"] == "2026-01-01"
    r = has("NJ", "algorithmic_rent_setting", "P.L. 2026, c. 43")
    assert r["status"] == "not_yet_effective" and r["effective_date"] == "2027-07-01"
    assert has("NJ", "application_screening_fees", "P.L. 2025, c. 405")["effective_date"] == "2026-05-01"
    assert has("San Francisco, CA", "algorithmic_rent_setting", "S.F. Admin. Code § 37.10C")["effective_date"] == "2024-10-14"


def test_rate_windows_are_not_law_start_dates():
    # yearly allowances ('effective March 1, 2026 through February 28, 2027') are not when a law took effect
    assert has("San Francisco, CA", "rent_increase_limits", "S.F. Admin. Code")["effective_date"] is None
    la = [r for r in R if r["jurisdiction"] == "Los Angeles, CA" and r["category"] == "rent_increase_limits"][0]
    assert la["effective_date"] is None


def test_no_noise_records():
    assert not [r for r in R if r["citation"] in ("N.J.S.A. 2A:18-61.28", "N.J.S.A. 2A:18-61.6")]
    assert not [r for r in R if r["jurisdiction"] == "San Diego, CA" and r["category"] == "rent_increase_limits"]
    assert not [r for r in R if r["jurisdiction"] == "Los Angeles, CA" and r["category"] == "algorithmic_rent_setting"]


def test_quote_quality():
    """Quotes are operative law text: no website menus, no bare headings, no bill digests, never cut mid-clause."""
    import re
    for r in R:
        if r.get("text_in_corpus") is False:
            continue
        q = r["quoted_span"]
        assert not re.search(r"skip to (?:main )?content|\bview text\b|\| *SF\.gov", q, re.I), (r["team_rule_id"], q[:80])
        assert not re.match(r"^\s*§?\s*\d+(?:\.\d+)+\s+[A-Z][^.]*$", q), (r["team_rule_id"], q)
        assert not re.match(r"^(?:The|This) bill would", q), r["team_rule_id"]
        if "fallback" in (r.get("notes") or ""):
            # the quote runs to the end of its clause (verification trims a final period)
            from citemap.corpus import load_manifest
            raw = {d.doc_id: d.raw for d in load_manifest()}[r["source_doc_id"]]
            i = raw.find(q)
            assert q.rstrip()[-1] in ".;" or raw[i + len(q):i + len(q) + 1] in (".", ";"), (r["team_rule_id"], q[-60:])


def test_expired_rate_window_has_no_value():
    la = [r for r in R if r["jurisdiction"] == "Los Angeles, CA" and r["category"] == "rent_increase_limits"][0]
    assert la["key_value"] is None and "current rate not in corpus" in la["notes"]


def test_mass_session_ordinals():
    assert not [r for r in R if re.search(r"\d+th Gen", r["citation"]) and re.search(r"(?:1|2|3)th Gen", r["citation"])]


import re  # noqa: E402


def test_rent_cap_wording_increase_the_rent():
    # hour-16 risk: 'increase the rent ... by more than N percent' must be read as a rent cap
    from citemap.extract.heuristic import score_sentence, key_value
    s = ("No landlord shall increase the rent of a residential dwelling unit in a building of five or more units "
         "by more than four percent (4%) in any twelve-month period.")
    assert score_sentence(s, "rent_increase_limits")[0] >= 7
    assert key_value(s, "rent_increase_limits") == "max 4%"


def test_no_rule_findings_exclude_link_only_cities():
    import json
    from citemap import config
    d = json.loads((config.OUTPUTS / "rules.json").read_text(encoding="utf-8"))
    nr = d["no_rule_findings"]
    assert nr and all(f["finding"] == "no_rule_at_this_level" for f in nr)
    assert not [f for f in nr if f["jurisdiction"] in ("Newark, NJ", "Hoboken, NJ")]   # unknowable, never 'no rule'
    have = {(r["jurisdiction"], r["category"]) for r in d["rules"]}
    assert not [f for f in nr if (f["jurisdiction"], f["category"]) in have]
