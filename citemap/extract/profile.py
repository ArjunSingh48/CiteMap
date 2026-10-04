"""Phase 2a: document profiling (no AI).

For every document: what kind of legal source it is, its official citation (derived from the
URL and the text, never typed by hand), its status, and its enacted / effective dates.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse, parse_qs, unquote

from ..corpus import Doc, HEADER_RE

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirtieth": 30,
            "ninetieth": 90, "sixtieth": 60, "twentieth": 20, "fifteenth": 15}
DATE_TXT = r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})"
DATE_NUM = r"(\d{1,2})/(\d{1,2})/(\d{2,4})"

from .. import config as _config
CITY_CODE = _config.CITY_CODES          # from data/jurisdictions.json


def parse_date(s: str) -> str | None:
    m = re.search(DATE_TXT, s, re.I)
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(DATE_NUM, s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def add_months_first_day(d: str, n: int) -> str:
    y, m = int(d[:4]), int(d[5:7])
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1).isoformat()


def add_days(d: str, n: int) -> str:
    return date.fromordinal(date.fromisoformat(d).toordinal() + n).isoformat()


@dataclass
class Profile:
    doc_id: str
    kind: str                     # statute | enacted_act | pending_bill | ordinance | guidance
    jurisdiction: str             # 'CA' or 'San Francisco, CA'
    level: str                    # state | city
    state: str
    base_citation: str
    status: str                   # in_force | not_yet_effective | pending | failed (doc-level default)
    enacted_date: str | None = None
    effective_date: str | None = None
    effective_evidence: str | None = None
    notes: list = field(default_factory=list)
    version_date: str | None = None        # 'Effective January 1, 2026' of the current text version (statutes)


def _jurisdiction(doc: Doc) -> tuple[str, str, str]:
    j = (doc.jurisdictions or "").split(";")[0].strip()
    if j in ("CA", "NJ", "MA"):
        return j, "state", j
    m = re.match(r"(.+),\s*(CA|NJ|MA)$", j)
    if m:
        return f"{m.group(1).strip()}, {m.group(2)}", "city", m.group(2)
    return j, "city", ""


def citation_from_url(url: str, text: str, juris: str, level: str, state: str) -> tuple[str, str]:
    """(citation, kind) derived from the source URL pattern."""
    u = urlparse(url)
    q = parse_qs(u.query)
    path = unquote(u.path)
    host = u.netloc.lower()
    if "leginfo.legislature.ca.gov" in host and "codes_displaySection" in path:
        code = {"CIV": "Civ.", "GOV": "Gov.", "BPC": "Bus. & Prof.", "CCP": "Civ. Proc.",
                "HSC": "Health & Saf."}.get(q.get("lawCode", [""])[0], q.get("lawCode", [""])[0])
        return f"Cal. {code} Code § {q.get('sectionNum', [''])[0]}", "statute"
    if "leginfo.legislature.ca.gov" in host and "billNavClient" in path:
        bid = q.get("bill_id", [""])[0]                     # 202520260AB325
        m = re.match(r"(\d{4})\d{4}\d([A-Z]+)(\d+)", bid)
        if m:
            return f"Cal. {m.group(2)} {m.group(3)} ({m.group(1)})", "bill"
    if "malegislature.gov" in host and "/Laws/GeneralLaws/" in path:
        m = re.search(r"Chapter(\w+)/Section([\w ~]+)", path)
        if m:
            sec = m.group(2).replace(" 1~2", "½").replace("1~2", "½").strip()
            return f"Mass. Gen. Laws ch. {m.group(1)}, § {sec}", "statute"
    if "malegislature.gov" in host and "/Bills/" in path:
        m = re.search(r"/Bills/(\d+)/([SH])(\d+)", path)
        if m:
            n = int(m.group(1))
            suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"Mass. {m.group(2)}.{m.group(3)} ({n}{suf} Gen. Court)", "bill"
    if "njleg" in host:
        m = re.search(r"/(?:PL|AL)(\d{2})/0*(\d+)_", path)
        if m:
            return f"P.L. 20{m.group(1)}, c. {int(m.group(2))}", "enacted_act"
    if level == "city":
        city = juris.split(",")[0]
        sec = dominant_section(text)
        code = CITY_CODE.get(city, f"{city} Mun. Code")
        if sec:
            return f"{code} {sec}", "ordinance" if ORDAINED.search(text[:6000]) else "guidance"
        return code, "guidance"
    return page_title(text) or url, "guidance"


def page_title(text: str) -> str | None:
    for ln in text.split("\n")[:15]:
        t = ln.strip()
        if 12 <= len(t) <= 140 and not t.lower().startswith(("skip", "http")):
            return t.split(" | ")[0]
    return None


ORDAINED = re.compile(r"(?i:BE IT ORDAINED|does ordain|hereby ordains)|^\s*ORDINANCE NO\.", re.M)
SEC_REF = re.compile(r"(?:§+\s*|\b[Ss]ec(?:tion)?\.?\s+|\bSECTION\s+|\bBMC\s+|\bLAMC\s+|\b[Cc]hapter\s+)(\d{1,3}(?:\.\d{1,4}){0,2}[A-Z]?)\b")


def dominant_section(text: str) -> str | None:
    """Most-referenced code location in a city document.
    If the text uses 3-part section numbers (13.63.030), 2-part numbers are chapters ('ch. 13.63');
    otherwise 2-part numbers are sections ('§ 37.9'). Ties go to the first one mentioned."""
    from collections import Counter
    refs = [(m.group(0), m.group(1)) for m in SEC_REF.finditer(text) if "." in m.group(1)]
    if not refs:
        return None
    has3 = any(len(r.split(".")) >= 3 for _, r in refs)
    cnt, first = Counter(), {}
    for i, (full, r) in enumerate(refs):
        parts = r.split(".")
        key = ".".join(parts[:2]) if (has3 or full.lower().startswith("chapter")) else r
        cnt[key] += 1
        first.setdefault(key, i)
    best = sorted(cnt, key=lambda k: (-cnt[k], first[k]))[0]
    return f"ch. {best}" if (has3 or len(best.split(".")) < 2) else f"§ {best}"


def find_enacted(text: str) -> str | None:
    for pat in (r"approved\s+" + DATE_TXT, r"Approved by Governor\s+" + DATE_TXT,
                r"Chaptered by Secretary of State[^\n]{0,40}?" + DATE_TXT,
                r"signed (?:into law )?(?:by the Governor )?on\s+" + DATE_TXT,
                r"(?:adopted|passed)\s+(?:by the City Council\s+)?on\s+" + DATE_TXT):
        m = re.search(pat, text, re.I)
        if m:
            return parse_date(m.group(0))
    m = re.search(r"Approved by Governor\s+(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I)
    if m:
        return parse_date(m.group(1))
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(?:Chaptered|Approved by (?:the )?Governor)", text, re.I)
    if m:                                       # leginfo bill history line '10/06/25 - Chaptered ...'
        return parse_date(m.group(1))
    return None


def find_effective(text: str, enacted: str | None, guidance: bool = False) -> tuple[str | None, str | None]:
    """Returns (date, evidence sentence). Explicit dates first, then relative rules."""
    flat = re.sub(r"\s+", " ", text)
    pats = [r"(?:take|took|went into|go into|goes into|takes) effect (?:on|as of)?\s*" + DATE_TXT]
    if not guidance:
        pats += [r"effective (?:on |as of |beginning )?" + DATE_TXT, r"operative (?:on )?" + DATE_TXT]
    for pat in pats:
        for m in re.finditer(pat, flat, re.I):
            after = flat[m.end(): m.end() + 40]
            ctx = flat[max(0, m.start() - 120): m.end() + 60]
            if re.match(r"\s*(?:,\s*)?(?:through|to|until|–|-)\s", after, re.I):
                continue                          # a rate window ('effective X through Y'), not a law's start date
            if re.search(r"relocation|surcharge|interest rate|allowable (?:annual )?increase|adjustment", ctx, re.I) and guidance:
                continue
            return parse_date(m.group(0)), flat[max(0, m.start() - 80): m.end() + 40]
    m = re.search(r"take effect on the (\w+) day of the (\w+) month (?:next )?following (?:the date of )?enactment", flat, re.I)
    if m and enacted:
        n = ORDINALS.get(m.group(2).lower())
        if n:
            return add_months_first_day(enacted, n), m.group(0)
    m = re.search(r"take effect (?:and be in force )?on the (\w+) day (?:from and )?after (?:its |the date of its )?(?:final )?(?:passage|adoption)", flat, re.I)
    if m and enacted:
        n = ORDINALS.get(m.group(1).lower())
        if n:
            return add_days(enacted, n), m.group(0)
    m = re.search(r"(?:shall )?take effect immediately", flat, re.I)
    if m and enacted:
        return enacted, m.group(0)
    return None, None


def profile(doc: Doc, as_of: str = "2026-10-01") -> Profile:
    m = HEADER_RE.match(doc.raw)
    body = doc.raw[m.end():] if m else doc.raw
    juris, level, state = _jurisdiction(doc)
    cit, kind = citation_from_url(doc.url, body, juris, level, state)
    p = Profile(doc.doc_id, kind, juris, level, state, cit, "in_force")
    if kind == "bill":
        enacted = find_enacted(body)
        chaptered = re.search(r"\bChaptered\b|Approved by Governor|signed by the Governor|Chapter \d+, Statutes of", body, re.I)
        if enacted or chaptered:
            p.kind = "enacted_act"
            p.enacted_date = enacted
        else:
            p.kind, p.status = "pending_bill", "pending"
            p.notes.append("bill page with no enactment evidence -> pending")
    if kind == "statute":
        m = re.search(r"(?:[Aa]dded|[Aa]mended|Repealed and added)\b[^\n]{0,140}?[Ee]ffective\s+" + DATE_TXT, body)
        if m:
            p.version_date = parse_date(m.group(0)[m.group(0).lower().rfind("effective"):])
    if p.level == "city" and re.search(r"\bI THEREFORE MOVE\b|\bTHEREFORE MOVE that\b", body):
        p.status = "pending"                   # a council motion asks for a law; it is not law yet
        p.notes.append("council motion -> pending")
    if p.kind in ("enacted_act", "ordinance", "guidance") and not p.enacted_date:
        p.enacted_date = find_enacted(body)
    if p.kind not in ("pending_bill", "statute"):   # statutes: dates are handled per sentence (2b)
        eff, ev = find_effective(body, p.enacted_date, guidance=(p.kind == "guidance"))
        if p.kind == "enacted_act" and not eff and p.base_citation.startswith("Cal. "):
            # California default: statutes enacted in year Y take effect Jan 1 of Y+1
            yr = int(p.enacted_date[:4]) if p.enacted_date else None
            if yr is None:
                ym = re.search(r"\((\d{4})\)", p.base_citation)
                yr = int(ym.group(1)) if ym else None
            if yr:
                eff, ev = f"{yr + 1}-01-01", "California default effective date (Jan 1 after enactment year)"
        p.effective_date, p.effective_evidence = eff, ev
        if eff and eff > as_of:
            p.status = "not_yet_effective"
    return p
