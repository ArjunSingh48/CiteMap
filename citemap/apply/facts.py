"""Building facts from the assessor sample, with gaps made explicit.

Units are kept as a range [units_min, units_max] because many rows don't give an exact
count but their use code does give a range (e.g. Boston 'APT 7-30 UNITS', Cambridge
'4-8-UNIT-APT', NJ class 4C = apartment building with 5+ units). A fact we don't have is
None, and the rules engine turns None into 'unknown'. Owner type is never in the data.
"""
from __future__ import annotations
import csv
import re
from dataclasses import dataclass, asdict

from .. import config


@dataclass
class Facts:
    year_built: int | None
    units_min: int | None
    units_max: int | None
    units_source: str
    use_code: str
    use_description: str
    owner_type: None = None       # never available (no owner names in public sample)

    def to_dict(self):
        return asdict(self)


def _int(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        f = float(s)
        if f != f or f in (float("inf"), float("-inf")) or f > 10**6:
            return None                      # NaN / infinity / absurd values are treated as missing
        v = int(f)
        return v if v > 0 else None
    except (ValueError, OverflowError):
        return None


def units_from_use(state: str, code: str, desc: str) -> tuple[int | None, int | None, str]:
    d = (desc or "").upper()
    us = re.findall(r"(\d+)\s*U\b", d)                    # NJ '6B-20U-G' -> 20; '3B-7U/4B-24U' -> 7 + 24
    if us:
        n = sum(int(x) for x in us)
        return n, n, "use description" + (" (sum of building parts)" if len(us) > 1 else "")
    m = re.search(r"(\d+)\s*-\s*(\d+)[\s-]*UNIT", d)        # 'APT 7-30 UNITS', '4-8-UNIT-APT'
    if m:
        return int(m.group(1)), int(m.group(2)), "use code range"
    m = re.search(r">\s*(\d+)[\s-]*UNIT", d)               # '>8-UNIT-APT'
    if m:
        return int(m.group(1)) + 1, None, "use code range"
    m = re.search(r"(\d+)\s*TO\s*(\d+)\s*UNITS", d)         # SF 'Apartment 5 to 14 Units'
    if m:
        return int(m.group(1)), int(m.group(2)), "use code range"
    m = re.search(r"(\d+)\s*UNITS?\s*OR\s*MORE", d)        # 'Apartment 15 Units or more'
    if m:
        return int(m.group(1)), None, "use code range"
    m = re.search(r"(\d+)\s*\+\s*UNITS", d)                # '5+ units'
    if m:
        return int(m.group(1)), None, "use code range"
    if "FIVE OR MORE" in d:
        return 5, None, "use code range"
    m = re.search(r"(\d+)\s*UNITS?\s*OR\s*LESS", d)
    if m:
        return 1, int(m.group(1)), "use code range"
    if state == "NJ" and (code or "").upper() == "4C":
        return 5, None, "NJ property class 4C (apartment, 5+ units)"
    return None, None, "not in data"


def facts_for(row: dict) -> Facts:
    yb = _int(row.get("year_built"))
    if yb and not (1700 <= yb <= 2030):
        yb = None
    u = _int(row.get("units"))
    cmin, cmax, csrc = units_from_use(row["state"], row.get("use_code", ""), row.get("use_description", ""))
    if u and cmin is not None and (u < cmin or (cmax is not None and u > cmax)):
        # the assessor count contradicts the use class / description (e.g. 2 units in an NJ class 4C '93U'
        # apartment building): trust the class / description and say so
        umin, umax, src = cmin, cmax, f"{csrc} (assessor count {u} disagrees; class/description used)"
    elif u:
        umin = umax = u
        src = "assessor unit count"
    else:
        umin, umax, src = cmin, cmax, csrc
    return Facts(yb, umin, umax, src, row.get("use_code", ""), row.get("use_description", ""))


def load_addresses() -> list[dict]:
    with open(config.ADDRESSES, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
