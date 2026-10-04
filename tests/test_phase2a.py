"""Phase 2a tests: document profiling (kind, citation, status, dates)."""
from citemap.corpus import docs_with_text
from citemap.extract.profile import profile, parse_date, add_months_first_day

P = {d.doc_id: profile(d) for d in docs_with_text()}


def test_state_statute_citations_from_url():
    assert P["D024"].base_citation == "Cal. Civ. Code § 1947.12"
    assert P["D027"].base_citation == "Cal. Gov. Code § 12955"
    assert P["D052"].base_citation == "Mass. Gen. Laws ch. 186, § 15B"
    assert P["D057"].base_citation == "Mass. Gen. Laws ch. 112, § 87DDD½"


def test_fair_act_enacted_not_yet_effective():
    p = P["D069"]
    assert p.base_citation == "P.L. 2026, c. 43"
    assert p.enacted_date == "2026-07-20" and p.effective_date == "2027-07-01"
    assert p.status == "not_yet_effective"


def test_ab325_in_force_from_2026():
    assert P["D022"].kind == "enacted_act" and P["D022"].effective_date == "2026-01-01"
    assert P["D022"].status == "in_force"


def test_ma_bills_pending():
    for i in ("D045", "D046", "D047", "D011"):
        assert P[i].status == "pending", i


def test_city_section_citations():
    assert P["D001"].base_citation == "Berkeley Mun. Code ch. 13.63"
    assert P["D079"].base_citation == "S.F. Admin. Code § 37.9"


def test_date_helpers():
    assert parse_date("approved July 20, 2026") == "2026-07-20"
    assert add_months_first_day("2026-07-20", 12) == "2027-07-01"
