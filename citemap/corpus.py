"""Load the law corpus: manifest + raw text + cleaned text for the model.

Raw text is kept untouched: quoted spans are always verified against the RAW text,
so every quote in rules.json is an exact substring of a corpus file.
"""
from __future__ import annotations
import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

HEADER_RE = re.compile(r"^SOURCE:\s*(?P<url>\S+)\s*\nRETRIEVED:\s*(?P<ret>[^\n]+)\n", re.M)

# Lines that are website chrome, not law.
NAV_WORDS = {
    "skip to content", "skip to main content", "home", "search", "menu", "login", "feedback",
    "sitemap", "accessibility", "faq", "print page", "go", "x", "share", "facebook", "twitter",
    "linkedin", "email", "print", "back to top", "close", "submit", "next", "previous", "prev",
}


@dataclass
class Doc:
    doc_id: str
    jurisdictions: str
    url: str
    source_type: str
    capture: str
    retrieved_at: str
    status: str
    text_path: Path | None
    raw: str = ""
    origin: str = "starter"          # starter | supplement | new_law
    sha256: str = ""
    lines: list = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(self.raw.strip())

    @property
    def retrieval_date(self) -> str:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", self.retrieved_at or "")
        return m.group(1) if m else ""

    @property
    def body(self) -> str:
        """Raw text without the SOURCE/RETRIEVED header."""
        m = HEADER_RE.match(self.raw)
        return self.raw[m.end():] if m else self.raw

    def clean_text(self) -> str:
        """Text for the model: website menus removed, numbered lines kept.
        Each kept line is prefixed with its line number so the model can point back."""
        out = []
        prev = None
        for i, line in enumerate(self.raw.split("\n")):
            s = line.strip()
            if not s or s == prev:
                continue
            low = s.lower()
            if low in NAV_WORDS:
                continue
            words = s.split()
            # drop very short menu-like lines with no digits/legal markers
            if len(words) <= 3 and not re.search(r"[\d§$%]", s) and not s.endswith(":"):
                continue
            out.append(f"L{i}: {s}")
            prev = s
        return "\n".join(out)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def load_manifest() -> list[Doc]:
    docs: list[Doc] = []
    with open(config.MANIFEST, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tp = (config.CORPUS_DIR / r["text_file"]) if r.get("text_file") else None
            d = Doc(
                doc_id=r["doc_id"], jurisdictions=r["jurisdictions"], url=r["url"],
                source_type=r["source_type"], capture=r["capture"], retrieved_at=r["retrieved_at"],
                status=r["status"], text_path=tp,
            )
            if tp and tp.exists():
                d.raw = _read(tp)
            docs.append(d)
    # Supplement: pages fetched in the morning for link-only sources (same doc_id)
    sup = config.SUPPLEMENT_DIR
    if sup.exists():
        by_id = {d.doc_id: d for d in docs}
        for p in sorted(sup.glob("D*.txt")):
            did = p.stem
            raw = _read(p)
            if did in by_id and not by_id[did].has_text and len(raw.split()) > 40:
                d = by_id[did]
                d.raw, d.text_path, d.origin = raw, p, "supplement"
                m = HEADER_RE.match(raw)
                if m:
                    d.retrieved_at = m.group("ret").strip()
    # New laws (e.g. hour-16 ordinance): any .txt in data/new_laws becomes a new doc
    nl = config.NEW_LAWS_DIR
    if nl.exists():
        for p in sorted(nl.glob("*.txt")):
            raw = _read(p)
            m = HEADER_RE.match(raw)
            juris = ""
            jm = re.search(r"^JURISDICTION:\s*(.+)$", raw, re.M)
            if jm:
                juris = jm.group(1).strip()
            docs.append(Doc(
                doc_id=f"N-{p.stem}", jurisdictions=juris, url=m.group("url") if m else f"file://{p.name}",
                source_type="official (new law)", capture="yes",
                retrieved_at=m.group("ret").strip() if m else "", status="ok", text_path=p,
                raw=raw, origin="new_law",
            ))
    for d in docs:
        d.sha256 = hashlib.sha256(d.raw.encode()).hexdigest()[:16] if d.raw else ""
    return docs


def docs_with_text() -> list[Doc]:
    return [d for d in load_manifest() if d.has_text]


if __name__ == "__main__":
    ds = load_manifest()
    wt = [d for d in ds if d.has_text]
    print(f"{len(ds)} docs in manifest, {len(wt)} with text")
    tot_raw = sum(len(d.raw) for d in wt)
    tot_clean = sum(len(d.clean_text()) for d in wt)
    print(f"raw chars {tot_raw:,}  clean chars {tot_clean:,}")
