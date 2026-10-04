"""Address -> legal jurisdiction stack (state, county, city).

The postal city is not always the legal city (Dorchester is in Boston, San Ysidro is in
San Diego). We resolve in this order:
 1. Census Geocoder result, if data/geocoded.csv exists (run tools/geocode_census.py on a
    machine with internet).
 2. The parcel dataset the row came from (each dataset covers one city's assessor roll) +
    a neighbourhood alias table for postal names.
Every address gets a `method` and a `confidence` so the UI and the audit log can show how
its city was decided.
"""
from __future__ import annotations
import csv
from dataclasses import dataclass

from .. import config

DATASET_CITY = {
    "LA County eGIS parcels": ("CA", "Los Angeles County", None),   # county roll: city from postal name
    "DataSF wv5m-vpq2 (2025 roll)": ("CA", "San Francisco County", "San Francisco"),
    "SANDAG/SanGIS parcels": ("CA", "San Diego County", None),
    "Alameda County parcels": ("CA", "Alameda County", None),
    "NJOGIS Parcels & MOD-IV Composite": ("NJ", None, None),
    "Boston Property Assessment FY2026": ("MA", "Suffolk County", "Boston"),
    "Cambridge Property Database FY2026 (waa7-ibdu)": ("MA", "Middlesex County", "Cambridge"),
}

NJ_COUNTY = {"Jersey City": "Hudson County", "Hoboken": "Hudson County", "Newark": "Essex County"}

# Postal place names that sit inside a legal city.
ALIASES = config.ALIASES                 # from data/jurisdictions.json

# Simple ZIP sanity ranges (first 3 digits) for the legal cities we cover.
ZIP_PREFIX_OK = {
    "Los Angeles": ("900", "910", "913", "914", "916"), "San Francisco": ("941",),
    "San Diego": ("921",), "Berkeley": ("947",), "Boston": ("021", "022"), "Cambridge": ("021",),
}


@dataclass
class Jurisdiction:
    state: str
    county: str | None
    city: str | None
    method: str
    confidence: float
    note: str = ""

    def stack(self) -> list[str]:
        out = [self.state]
        if self.city:
            out.append(f"{self.city}, {self.state}")
        return out


def _load_geocoded() -> dict:
    p = config.GEOCODED
    if not p.exists():
        return {}
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["address_id"]] = r
    return out


_GEO = None


def resolve(row: dict) -> Jurisdiction:
    global _GEO
    if _GEO is None:
        _GEO = _load_geocoded()
    st = row["state"].strip()
    postal = row["postal_city"].strip()
    ds = row.get("source_dataset", "")

    # 1. Census geocoder (authoritative when present and matched)
    g = _GEO.get(row["address_id"])
    if g and g.get("place") and g.get("match") == "Match":
        place = g["place"].replace(" city", "").replace(" City", "").strip()
        known = [c for c in config.CITIES.get(st, []) if c.lower() == place.lower()]
        if known:
            return Jurisdiction(st, g.get("county") or None, known[0], "census_geocoder", 0.97)
        return Jurisdiction(st, g.get("county") or None, None, "census_geocoder", 0.95,
                            f"Census places this address in '{g['place']}', outside the cities in scope")

    _, county, ds_city = DATASET_CITY.get(ds, (st, None, None))
    city = ds_city
    method, conf, note = "parcel_dataset", 0.9, ""
    if city is None:
        known = [c for c in config.CITIES.get(st, []) if c.lower() == postal.lower()]
        if known:
            city, method = known[0], "postal_city"
        elif (st, postal.lower()) in ALIASES:
            city, method = ALIASES[(st, postal.lower())], "neighbourhood_alias"
            note = f"Postal name '{postal}' is a neighbourhood inside {ALIASES[(st, postal.lower())]}"
    elif postal.lower() != city.lower():
        if (st, postal.lower()) in ALIASES and ALIASES[(st, postal.lower())] == city:
            method, note = "neighbourhood_alias", f"Postal name '{postal}' is a neighbourhood inside {city}"
        else:
            note = f"Postal name '{postal}' differs from dataset city {city}"
            conf = 0.8
    if st == "NJ" and city:
        county = NJ_COUNTY.get(city)
        note = (note + "; " if note else "") + "NJ ZIP in assessor data is often the owner's mailing ZIP; municipality field used"
    # ZIP sanity check (CA/MA only; NJ ZIPs are owner mailing ZIPs)
    z = (row.get("zip") or "").strip()
    if city in ZIP_PREFIX_OK and z and not z.startswith(ZIP_PREFIX_OK[city]):
        conf = min(conf, 0.7)
        note = (note + "; " if note else "") + f"ZIP {z} unusual for {city}: confirm with Census geocoder"
    if city is None:
        conf = 0.3
        note = note or f"Could not place postal city '{postal}' in a covered city"
    return Jurisdiction(st, county, city, method, conf, note)
