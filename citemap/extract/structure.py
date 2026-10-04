"""Module A, steps 1-4: read each document, extract candidate rules, verify each one.

Agentic flow per document:
  TRIAGE (manifest metadata) -> EXTRACT (LLM, chunked) -> VERIFY (schema, exact quote,
  dates, enums) -> REPAIR (LLM re-quotes once if the quote is not found) -> keep / drop.
If the LLM is unavailable the heuristic extractor runs instead (fail-open).
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config
from ..audit import log
from ..corpus import Doc
from . import prompts, verify
from .llm import complete_json, LLMUnavailable

CHUNK_CHARS = 55000
VALID_STATUS = {"in_force", "not_yet_effective", "pending", "failed"}
FACTS = {"year_built", "building_age_years", "units", "owner_type", "owner_occupied", "unit_type",
         "landlord_unit_count"}
OPS = {"<", "<=", ">", ">=", "==", "!=", "in"}

STATE_NAMES = {"california": "CA", "new jersey": "NJ", "massachusetts": "MA", "ca": "CA", "nj": "NJ", "ma": "MA"}


def norm_jurisdiction(j: str, level: str | None) -> tuple[str, str]:
    s = (j or "").strip()
    low = s.lower().replace(".", "")
    if low in STATE_NAMES:
        return STATE_NAMES[low], "state"
    for st, cities in config.CITIES.items():
        for c in cities:
            if c.lower() in low:
                return f"{c}, {st}", "city"
    return s, (level or "city")


def chunks(doc: Doc) -> list[str]:
    text = doc.clean_text()
    if len(text) <= CHUNK_CHARS:
        return [text]
    lines = text.split("\n")
    out, cur, size = [], [], 0
    for ln in lines:
        if size + len(ln) > CHUNK_CHARS and cur:
            out.append("\n".join(cur))
            cur = cur[-15:]           # small overlap so a rule split at a boundary survives
            size = sum(len(x) for x in cur)
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def _clean_cond(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    f, op = c.get("fact"), c.get("op")
    if f not in FACTS or op not in OPS:
        return None
    out = {"fact": f, "op": op, "value": c.get("value")}
    if c.get("basis"):
        out["basis"] = c["basis"]
    return out


def to_record(raw: dict, doc: Doc, as_of: str) -> tuple[dict | None, str]:
    """Convert one model rule into an official rule record, verifying everything."""
    cat = raw.get("category")
    if cat not in config.CATEGORIES:
        return None, f"bad category {cat}"
    juris, level = norm_jurisdiction(raw.get("jurisdiction", ""), raw.get("level"))
    status = raw.get("status") if raw.get("status") in VALID_STATUS else None
    eff = verify.clean_date(raw.get("effective_date"))
    if status in ("in_force", "not_yet_effective") or status is None:
        s2 = verify.status_as_of("in_force", eff, as_of)
        status = "not_yet_effective" if s2 == "not_yet_effective" else "in_force"
    # quote: exact match -> fuzzy snap -> line numbers -> (repair later)
    span, how = verify.locate_quote(doc.raw, raw.get("quoted_span") or "")
    if not span:
        ls = raw.get("quote_line_start")
        le = raw.get("quote_line_end")
        try:
            span = verify.quote_from_lines(doc.raw, int(ls) if ls is not None else None,
                                           int(le) if le is not None else None)
            how = "line_numbers" if span else how
        except (TypeError, ValueError):
            span = None
    cov = raw.get("coverage") or {}
    conds = [x for x in (_clean_cond(c) for c in (cov.get("all") or [])) if x]
    ex = []
    for clause in cov.get("exempt_any") or []:
        if isinstance(clause, dict):
            clause = [clause]
        if isinstance(clause, list):
            cc = [x for x in (_clean_cond(c) for c in clause) if x]
            if cc:
                ex.append(cc)
    rec = {
        "team_rule_id": "",
        "jurisdiction": juris,
        "level": level,
        "category": cat,
        "status": status,
        "title": (raw.get("title") or "").strip()[:200] or f"{juris} {cat}",
        "requirement": (raw.get("requirement") or "").strip(),
        "key_value": raw.get("key_value"),
        "coverage_conditions": {
            "text": raw.get("coverage_text") or "",
            "all": conds,
            "exempt_any": ex,
            "defers_to_local": bool(cov.get("defers_to_local")),
        },
        "exemptions": raw.get("exemptions"),
        "overrides": [],
        "interaction": None,
        "effective_date": eff,
        "citation": (raw.get("citation") or "").strip(),
        "source_doc_id": doc.doc_id,
        "source_url": doc.url,
        "quoted_span": span or "",
        "confidence": float(raw["confidence"]) if isinstance(raw.get("confidence"), (int, float)) else None,
        "conflict_flag": False,
        "conflict_note": None,
        # extra fields (allowed by the schema) for audit and UI
        "retrieval_date": doc.retrieval_date,
        "enacted_date": verify.clean_date(raw.get("enacted_date")),
        "penalty": raw.get("penalty"),
        "notes": raw.get("notes"),
        "quote_check": how if span else "pending_repair",
        "extracted_by": "llm",
        "source_origin": doc.origin,
        "current_version_effective": raw.get("current_version_effective"),
    }
    if not rec["citation"]:
        return None, "no citation"
    if not rec["requirement"]:
        return None, "no requirement"
    return rec, "ok"


def repair_quote(rec: dict, doc: Doc, chunk_text: str) -> dict:
    try:
        out = complete_json(prompts.REPAIR_SYSTEM,
                            f"RULE:\n{json.dumps({k: rec[k] for k in ('title', 'requirement', 'key_value', 'citation')})}\n\n"
                            f"DOCUMENT:\n{chunk_text}", max_tokens=1500, tag=f"repair:{doc.doc_id}")
    except LLMUnavailable:
        return rec
    q = (out or {}).get("quoted_span") if isinstance(out, dict) else None
    span, how = verify.locate_quote(doc.raw, q or "")
    if not span:
        try:
            span = verify.quote_from_lines(doc.raw, int(out.get("quote_line_start")), int(out.get("quote_line_end")))
            how = "repair_lines" if span else how
        except (TypeError, ValueError, AttributeError):
            span = None
    if span:
        rec["quoted_span"], rec["quote_check"] = span, f"repaired:{how}"
    return rec


def extract_doc(doc: Doc, as_of: str = config.DEFAULT_AS_OF) -> list[dict]:
    parts = chunks(doc)
    records = []
    for pi, text in enumerate(parts):
        part = f"Part {pi + 1} of {len(parts)} of this document." if len(parts) > 1 else ""
        user = prompts.USER_TEMPLATE.format(
            as_of=as_of, doc_id=doc.doc_id, jurisdictions=doc.jurisdictions, url=doc.url,
            source_type=doc.source_type, retrieved=doc.retrieved_at, part=part, text=text)
        out = complete_json(prompts.SYSTEM, user, tag=f"extract:{doc.doc_id}:{pi}")
        raws = out.get("rules", []) if isinstance(out, dict) else (out if isinstance(out, list) else [])
        for raw in raws:
            rec, why = to_record(raw, doc, as_of)
            if rec is None:
                log("rule_rejected", doc_id=doc.doc_id, reason=why, title=raw.get("title"))
                continue
            if rec["quote_check"] == "pending_repair":
                rec = repair_quote(rec, doc, text)
            if not verify.is_exact_substring(doc.raw, rec["quoted_span"]) or len(rec["quoted_span"]) < 20:
                log("rule_dropped_no_quote", doc_id=doc.doc_id, title=rec["title"], citation=rec["citation"])
                continue
            records.append(rec)
    log("doc_extracted", doc_id=doc.doc_id, parts=len(parts), rules=len(records))
    return records


def extract_all(docs: list[Doc], as_of: str = config.DEFAULT_AS_OF, workers: int = 6) -> list[dict]:
    from .heuristic import extract_doc_heuristic
    use_llm = config.has_llm()
    log("extract_start", docs=len(docs), mode="llm" if use_llm else "heuristic", model=config.LLM_MODEL)
    results: list[dict] = []
    if not use_llm:
        for d in docs:
            results.extend(extract_doc_heuristic(d, as_of))
        return results
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(extract_doc, d, as_of): d for d in docs}
        for f in as_completed(futs):
            d = futs[f]
            try:
                results.extend(f.result())
            except Exception as e:  # fail-open per document
                log("doc_failed", doc_id=d.doc_id, error=str(e)[:300])
                failed.append(d)
    for d in failed:   # fallback for any document the model could not process
        results.extend(extract_doc_heuristic(d, as_of))
    return results
