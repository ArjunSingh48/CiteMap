"""Phase 2b tests: heuristic extractor picks the right sentence and value."""
from citemap.corpus import docs_with_text
from citemap.extract.heuristic import extract_doc_heuristic, key_value

R = {d.doc_id: extract_doc_heuristic(d) for d in docs_with_text()}


def get(doc, cat):
    return [r for r in R[doc] if r["category"] == cat]


def test_all_quotes_are_exact_substrings():
    docs = {d.doc_id: d for d in docs_with_text()}
    for did, rs in R.items():
        for r in rs:
            assert r["quoted_span"] in docs[did].raw, (did, r["quoted_span"][:60])


def test_ca_rent_cap():
    r = get("D024", "rent_increase_limits")[0]
    assert r["key_value"] == "5% + CPI, max 10%" and "Cal. Civ. Code § 1947.12" in r["citation"]


def test_ca_deposit_one_month():
    assert get("D025", "security_deposits")[0]["key_value"] == "1 month's rent"


def test_ca_screening_fee_cap():
    assert "$30" in get("D026", "application_screening_fees")[0]["key_value"]


def test_nj_deposit_from_guide_with_statute_cite():
    cites = {r["citation"]: r for r in get("D067", "security_deposits")}
    assert cites["N.J.S.A. 46:8-21.2"]["key_value"] == "1.5 months' rent"
    assert cites["N.J.S.A. 46:8-21.2"]["jurisdiction"] == "NJ"


def test_fair_act_found_and_not_yet_effective():
    r = get("D069", "algorithmic_rent_setting")[0]
    assert r["status"] == "not_yet_effective" and r["effective_date"] == "2027-07-01"


def test_ma_bills_pending_and_no_local_rent_control():
    assert all(r["status"] == "pending" for d in ("D045", "D046") for r in R[d])
    assert get("D048", "rent_increase_limits")[0]["key_value"] == "No local rent control allowed"


def test_la_motion_is_not_a_ban():
    assert not get("D039", "algorithmic_rent_setting")


def test_key_value_patterns():
    assert key_value("may not exceed one and one-half times one month’s rent", "security_deposits") == "1.5 months' rent"
