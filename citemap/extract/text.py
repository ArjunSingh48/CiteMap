"""Turn raw corpus text into paragraphs/sentences with exact raw offsets."""
from __future__ import annotations
import re
from dataclasses import dataclass

ENUM_START = re.compile(r"^\s*(\(?[a-z0-9]{1,4}[\).]|§|sec\.|section\b|\d+\.\s|[A-Z]\.\s|•|-\s|\*)", re.I)
SECTION_HDR = re.compile(
    r"^\s*(?:SEC(?:TION)?\.?\s*)?(\d{1,4}(?:\.\d{1,4}){1,3}[A-Z]?)\.?\s*(?:$|[A-Z(])")


@dataclass
class Para:
    start: int          # raw offset
    end: int
    text: str           # raw text (may include newlines)
    line_no: int
    section: str | None  # nearest preceding section number, if any

    @property
    def flat(self) -> str:
        return re.sub(r"\s+", " ", self.text).strip()


def paragraphs(raw: str, body_start: int = 0) -> list[Para]:
    """Group wrapped lines into paragraphs. A new paragraph starts after a blank line,
    or when a line starts with an enumerator, or when the previous line ended a sentence
    and this one starts with a capital letter."""
    lines = raw.split("\n")
    offs = []
    o = 0
    for ln in lines:
        offs.append(o)
        o += len(ln) + 1
    paras: list[Para] = []
    cur_start = None
    cur_line = 0
    cur_end = 0
    section = None
    cur_section = None

    def flush():
        nonlocal cur_start
        if cur_start is not None:
            txt = raw[cur_start:cur_end]
            if txt.strip():
                paras.append(Para(cur_start, cur_end, txt, cur_line, cur_section))
        cur_start = None

    prev = ""
    for i, ln in enumerate(lines):
        if offs[i] < body_start:
            continue
        s = ln.strip()
        if not s:
            flush()
            prev = ""
            continue
        m = SECTION_HDR.match(s)
        if m and len(s) < 160:
            section = m.group(1)
        new = (cur_start is None or ENUM_START.match(s)
               or (prev.rstrip().endswith((".", ":", ";")) and s[:1].isupper() and len(prev) < 90)
               or (len(prev) < 40 and not (s[:1].islower() or s[:1].isdigit() or s[:1] in ".,;%")
                   and not prev.rstrip().endswith((",", "(", "-")) and not re.search(r"\b(effective|is|of|the|and|or|to)$", prev.rstrip(), re.I)))
        if new:
            flush()
            cur_start = offs[i] + (len(ln) - len(ln.lstrip()))
            cur_line = i
            cur_section = section
        cur_end = offs[i] + len(ln.rstrip())
        prev = s
    flush()
    return paras


SENT_SPLIT = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"“])")


HEADING_BREAK = re.compile(r"(?<=[^\s,;:(\-])[ \t]*\n(?=[ \t]*[A-Z(§“\"])")


def _is_heading(line: str) -> bool:
    s = line.strip()
    return bool(s) and len(s) < 110 and not s.endswith((".", ",", ";", ":")) and (
        s.isupper() or len(s.split()) <= 12)


def sentences(p: Para) -> list[tuple[int, int, str]]:
    """Sentences inside a paragraph as (raw_start, raw_end, flat_text).
    Heading lines (short, no end punctuation, next line capitalised) are split off."""
    out = []
    for a, b in _heading_split(p):
        out.extend(_sentences_in(p, a, b))
    return out


def _heading_split(p: Para):
    t = p.text
    cuts = [0]
    pos = 0
    for line in t.split("\n")[:-1]:
        end = pos + len(line)
        nxt = t[end + 1:end + 2]
        if _is_heading(line) and (nxt.isupper() or nxt in "(§“\""):
            cuts.append(end + 1)
        pos = end + 1
    cuts.append(len(t))
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if t[cuts[i]:cuts[i + 1]].strip()]


def _sentences_in(p: Para, lo: int, hi: int):
    out = []
    t = p.text[lo:hi]
    base = p.start + lo
    pos = 0
    for m in SENT_SPLIT.finditer(t):
        seg = t[pos:m.start()]
        if seg.strip():
            a = base + pos + (len(seg) - len(seg.lstrip()))
            out.append((a, base + m.start(), re.sub(r"\s+", " ", seg).strip()))
        pos = m.end()
    seg = t[pos:]
    if seg.strip():
        a = base + pos + (len(seg) - len(seg.lstrip()))
        out.append((a, base + pos + len(seg.rstrip()), re.sub(r"\s+", " ", seg).strip()))
    return out
