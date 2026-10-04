"""Verification: every rule must pass the official schema and quote the source EXACTLY.

Quote checking works on a whitespace/typography-normalised view of the raw document with an
index map back to the raw text, so the quoted_span we write is always an exact substring of
the corpus file (line breaks included), even when the model joined wrapped lines.
"""
from __future__ import annotations
import re
from datetime import date

from rapidfuzz import fuzz

TYPO = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-",
                      "—": "-", " ": " ", "§": "§"})


def _norm_char(c: str) -> str:
    return c.translate(TYPO).lower()


class NormText:
    """Normalised text with a map from normalised index -> raw index."""

    def __init__(self, raw: str):
        self.raw = raw
        chars, idx = [], []
        prev_space = True
        for i, c in enumerate(raw):
            if c.isspace():
                if not prev_space:
                    chars.append(" ")
                    idx.append(i)
                prev_space = True
            else:
                chars.append(_norm_char(c))
                idx.append(i)
                prev_space = False
        self.norm = "".join(chars)
        self.idx = idx

    def raw_span(self, ns: int, ne: int) -> str:
        """raw substring covering normalised [ns, ne)."""
        a = self.idx[ns]
        b = self.idx[ne - 1] + 1
        return self.raw[a:b]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(TYPO).lower()).strip()


def locate_quote(doc_raw: str, quote: str, nt: NormText | None = None) -> tuple[str | None, str]:
    """Return (exact raw substring, method) or (None, reason)."""
    if not quote or len(quote.strip()) < 10:
        return None, "empty"
    nt = nt or NormText(doc_raw)
    q = norm(quote).strip(" .\"'")
    if len(q) < 10:
        return None, "too short"
    i = nt.norm.find(q)
    if i >= 0:
        return nt.raw_span(i, i + len(q)), "exact"
    # fuzzy alignment (model may have dropped a word or changed punctuation)
    al = fuzz.partial_ratio_alignment(q, nt.norm, score_cutoff=90)
    if al and al.score >= 90 and (al.dest_end - al.dest_start) >= 20:
        s, e = al.dest_start, al.dest_end
        return nt.raw_span(s, e), f"fuzzy:{al.score:.0f}"
    return None, "not found"


def quote_from_lines(doc_raw: str, start: int | None, end: int | None) -> str | None:
    if start is None:
        return None
    lines = doc_raw.split("\n")
    end = end if end is not None else start
    if not (0 <= start < len(lines)) or end < start or end - start > 6:
        return None
    seg = "\n".join(lines[start:end + 1]).strip()
    if len(seg) < 20:
        return None
    return seg[:600]


def is_exact_substring(doc_raw: str, span: str) -> bool:
    return bool(span) and span in doc_raw


# ---------- dates & status ----------
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def clean_date(v) -> str | None:
    if v in (None, "", "null"):
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})$", s)
    if m:
        return s
    return None


def date_floor(d: str | None) -> str | None:
    """Earliest possible full date for a partial date (used for 'is it effective yet')."""
    if not d:
        return None
    if len(d) == 4:
        return f"{d}-01-01"
    if len(d) == 7:
        return f"{d}-01"
    return d


def status_as_of(status: str, effective_date: str | None, as_of: str) -> str:
    """Re-derive status for any query date. pending/failed never change by date."""
    if status in ("pending", "failed"):
        return status
    eff = date_floor(effective_date)
    if eff and eff > as_of:
        return "not_yet_effective"
    if eff and len(effective_date) < 10 and effective_date[:len(effective_date)] == as_of[:len(effective_date)]:
        # partial date in the same month/year as the query: can't be sure
        return "in_force_uncertain"
    return "in_force"
