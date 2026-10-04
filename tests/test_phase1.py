"""Phase 1 tests: corpus, schema, jurisdiction, facts, quote verification."""
import json
from citemap import config, schema
from citemap.corpus import load_manifest
from citemap.apply.facts import load_addresses, facts_for
from citemap.resolve.jurisdiction import resolve
from citemap.extract import verify
from citemap.extract.text import paragraphs


def test_corpus_counts():
    ds = [d for d in load_manifest() if d.origin != "new_law"]     # added laws (hour 16) don't count
    assert len(ds) == 87
    assert sum(d.has_text and d.origin == "starter" for d in ds) == 54


def test_official_sample_record_valid():
    rec = json.loads((config.STARTER / "schema" / "sample_rule_record.json").read_text(encoding="utf-8"))
    assert schema.is_valid(rec), schema.errors(rec)


def test_all_500_addresses_resolve_to_a_covered_city():
    rows = load_addresses()
    assert len(rows) == 500
    for r in rows:
        j = resolve(r)
        assert j.city in config.CITIES[j.state], (r["address_id"], r["postal_city"])


def test_neighbourhood_aliases():
    assert resolve({"address_id": "x", "state": "MA", "postal_city": "Dorchester", "zip": "02124",
                    "source_dataset": "Boston Property Assessment FY2026"}).city == "Boston"
    assert resolve({"address_id": "y", "state": "CA", "postal_city": "San Ysidro", "zip": "92173",
                    "source_dataset": "SANDAG/SanGIS parcels"}).city == "San Diego"


def test_unit_ranges_from_use_codes():
    f = facts_for({"state": "MA", "year_built": "1900", "units": "", "use_code": "A/112", "use_description": "APT 7-30 UNITS"})
    assert (f.units_min, f.units_max) == (7, 30)
    f = facts_for({"state": "NJ", "year_built": "", "units": "", "use_code": "4C", "use_description": "6B-20U-G"})
    assert (f.units_min, f.units_max) == (20, 20)
    f = facts_for({"state": "CA", "year_built": "", "units": "", "use_code": "7700", "use_description": "Alameda County use code (5+ units)"})
    assert f.units_min == 5 and f.year_built is None


def test_quote_snaps_to_exact_raw_text_across_line_breaks():
    d = {x.doc_id: x for x in load_manifest()}["D069"]
    q = "This act shall be known and may be cited as the “Forbidding the Algorithmic Inflation of Rent (FAIR) Act.”"
    span, how = verify.locate_quote(d.raw, q)
    assert span and span in d.raw, how


def test_status_as_of():
    assert verify.status_as_of("in_force", "2027-07-01", "2026-10-01") == "not_yet_effective"
    assert verify.status_as_of("in_force", "2027-07-01", "2027-07-02") == "in_force"
    assert verify.status_as_of("pending", None, "2030-01-01") == "pending"


def test_section_headers_detected():
    d = {x.doc_id: x for x in load_manifest()}["D024"]
    assert any(p.section == "1947.12" for p in paragraphs(d.raw))
