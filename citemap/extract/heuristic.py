"""Phase 2b: the non-AI extractor.

For each document: split into sentences (exact raw offsets), score every sentence for each
of the 6 categories (topic words x obligation words x numbers - procedural words), keep the
categories the document is really about, pick the best sentence per category as the quote,
and pull out the headline value, a sentence-level citation and sentence-level dates.
Output is in the same 'raw rule' shape the LLM path produces, so both paths share the same
verification code (structure.to_record).
"""
from __future__ import annotations
import re

from .. import config
from ..audit import log
from ..corpus import Doc, HEADER_RE
from .profile import profile, parse_date, DATE_TXT
from .text import paragraphs, sentences

CAT_TERMS = {
    "rent_increase_limits": (
        r"rent (?:increase|control|stabiliz\w*|cap)|increase (?:the |any )?(?:gross )?(?:rent|rental rate)\b|allowable (?:annual )?(?:rent )?increase"
        r"|annual general adjustment|rent ceiling|rent controlled|rent increases? (?:are|is) limited",
        r"cost of living|consumer price|\bcpi\b|rental rate|base rent"),
    "just_cause_eviction": (
        r"just cause|without (?:just |good )?cause|terminate (?:a |the )?tenancy|evict(?:ion)?s?\b|notice to quit|reprisals?\b|retaliat\w*"
        r"|determine the lease|estates? at will",
        r"relocation assistance|no-fault|at-fault|recover possession|notice of (?:tenants'? )?rights"),
    "security_deposits": (r"security deposit|security\b.{0,80}\bin excess of|deposit.{0,30}(?:month|rent)",
                          r"deposit|last month'?s rent|rent in advance"),
    "application_screening_fees": (r"application (?:screening )?fee|screening fee|application or other similar fee|broker'?s? fee|fee to (?:apply|lease)|at or prior to the commencement of any tenancy|pay any amount in excess of",
                                   r"credit report|finding dwelling accommodations[^.]{0,60}\bfee"),
    "screening_restrictions": (r"criminal (?:history|record|background|conviction)|arrests?\b|convictions?\b|source of income|fair chance",
                               r"housing (?:choice )?voucher|section 8|public assistance"),
    "algorithmic_rent_setting": (r"algorithm\w*|pricing device|coordinated pricing|coordinating function|price.?fixing|rent.setting (?:software|tool)",
                                 r"nonpublic competitor|revenue management"),
}
DEON_STRONG = re.compile(
    r"\b(shall not|may not|must not|cannot|can.t|is unlawful|unlawful practice|it shall be unlawful|it is unlawful|unlawful for|prohibit\w*|"
    r"no (?:landlord|owner|lessor|person|city or town|housing provider)|not (?:be )?(?:greater|more) than|not exceed|"
    r"in excess of|limited to|capped|maximum|without just cause|may no longer|in no case|greater than|discriminat\w* against|"
    r"must have (?:a )?[\"“]?(?:just|good) cause|(?:must|shall) be returned within|(?:must|shall) pay (?:\w+ ){0,2}interest|to ban|it is required that|will not|protects? (?:\w+ ){0,3}(?:residents|tenants|applicants|people))\b", re.I)
DEON_WEAK = re.compile(r"\b(shall|must|required|requires|is entitled|may be determined)\b", re.I)
VALUE = re.compile(r"\d+(?:\.\d+)?\s*(?:percent|%)|month['’]?s['’]?\s+rent|\$\s?\d|one and one-half|twice the", re.I)
PROCEDURAL = re.compile(
    r"\b(receipt|record|photograph|inspection|itemiz\w+|statement|copy|copies|file with|filed with|registration|"
    r"hearing|petition|form\b|definition|means\b|as used in|for the purposes of|calculator|website|email|"
    r"interest\b|escrow|appeal|testimony|subpoena)", re.I)
EXPLICIT_CITE = re.compile(
    r"(N\.J\.S\.A\.\s*\d+[A-Z]?:\d+[A-Z]?-[\d.]+[a-z]?|(?:Cal(?:ifornia)?\.?\s*)?Civ(?:il|\.)\s*Code\s*(?:§+|Section)\s*[\d.]+"
    r"|G\.\s?L\.\s*c\.\s*\d+[A-Z]?,?\s*§+\s*\w+|P\.L\.\s?\d{4},?\s*c\.\s?\d+|Gov(?:ernment|\.)\s*Code\s*(?:§+|Section)\s*[\d.]+"
    r"|(?:BMC|LAMC|SDMC)\s*[\d.]+|§+\s*\d+\.\d+[A-Z]?)")
STATE_CITE = re.compile(r"N\.J\.S\.A|Civ(?:il|\.)\s*Code|G\.\s?L\.|P\.L\.|Gov(?:ernment|\.)\s*Code", re.I)

CHROME = re.compile(r"skip to (?:main )?content|\|\s*[\w.]+\.(?:gov|org|com)\b|\bview text\b|\bcookies?\b|\bsign in\b", re.I)
DIGEST = re.compile(r"^(?:The|This) bill would\b|^Existing law\b|^This (?:Ordinance|Act|activity)\b|^The Council wishes\b|\bCEQA\b", re.I)
HEADING = re.compile(r"^\s*(?:§+\s*)?\d+(?:\.\d+)+[A-Z]?\.?\s+[A-Z][^.;:]{0,100}\.?\s*$")
PARTY = re.compile(r"\b(?:for|by) (?:a|an|any|the) (?:landlord|owner|lessor|housing provider)\b|^(?:\(\w+\)\s*)?(?:A|An|Any|The|No) (?:landlord|owner|lessor|housing provider)\b", re.I)          # Legislative Counsel's summary, not the law
NEGATED = re.compile(r"^This (?:cap|limit|rule|law|ordinance) does not\b|\bdoes not prevent\b", re.I)
LICENSING = re.compile(r"engage in the business of", re.I)
SECTION_PENALTY = re.compile(r"finding|purpose|intent|definition|title|short title|severab", re.I)
ROMAN_ITEM = re.compile(r"^\(\s*(?:i{1,3}|iv|v|vi{1,3}|ix|x)\s*\)", re.I)
LETTER_ITEM = re.compile(r"^\(\s*[a-hj-uw-z]\s*\)")

DEFINITION = re.compile(r"[”\"]\s*(?:shall )?means\b|\bmeans\s+(?:any|a|an|the)\b|as used in this|for (?:the )?purposes? of this", re.I)
PURPOSE = re.compile(r"purpose and intent|the legislature finds|finds and declares|whereas|was (?:created|enacted|adopted) to\b", re.I)

LABEL = {
    "rent_increase_limits": "Rent increase limit", "just_cause_eviction": "Just-cause eviction protection",
    "security_deposits": "Security deposit limit", "application_screening_fees": "Application / screening fee rule",
    "screening_restrictions": "Tenant screening restriction", "algorithmic_rent_setting": "Algorithmic rent-setting ban",
}


def score_sentence(s: str, cat: str, as_of: str = config.DEFAULT_AS_OF, inherit: bool = False,
                   section_title: str = "") -> tuple[float, int]:
    """inherit: the sentence is a list item '(iii) ...' under a lead-in like 'no lessor may require ... the
    following:', so it carries the lead-in's obligation. section_title: the code section heading it sits under."""
    strong, weak = CAT_TERMS[cat]
    hits = 2 * len(re.findall(strong, s, re.I)) + len(re.findall(weak, s, re.I))
    if hits == 0:
        return 0.0, 0
    deon = 3 if (DEON_STRONG.search(s) or inherit) else (1 if DEON_WEAK.search(s) else 0)
    if cat == "rent_increase_limits" and re.search(r"(?:allowable|annual)[^.]{0,60}increases?[^.]{0,170}\bis\s+\d+(?:\.\d+)?\s*%", s, re.I):
        deon = 3 
    if cat == "security_deposits" and re.search(r"security deposit interest\W{0,5}\d+(?:\.\d+)?\s*%", s, re.I):
        deon = 3                                  # published deposit-interest rate is the rule itself
    if deon == 0:
        return 0.0, hits
    sc = min(hits, 6) + deon * 2
    if VALUE.search(s):
        sc += 2
    sc -= min(2 * len(PROCEDURAL.findall(s)), 6)
    n = len(s)
    if n < 50:
        sc -= 3
    elif n > 450:
        sc -= 2
    if s.endswith(":"):
        sc -= 1 if DEON_STRONG.match(s) or re.match(r"no ", s, re.I) else 3
    m_end = re.search(r"through\s+" + DATE_TXT, s, re.I)
    if m_end and (parse_date(m_end.group(0)) or "9999") < as_of and re.search(r"prohibit|freeze|suspend", s, re.I):
        sc -= 10                                 # a rule that already expired is not today's rule
    words = re.findall(r"[A-Za-z]{3,}", s)
    label_value = bool(re.match(r"^[A-Z][A-Za-z ]{3,50}: \d", s))   # 'Security Deposit Interest: 4.2% for ...'
    if label_value:
        sc += 4                                  # a published rate line is a statement, not a heading
    if words and sum(w[0].isupper() for w in words) / len(words) > 0.6 and not label_value:
        sc -= 4                                  # headings / titles are not rule statements
    if DEFINITION.search(s):
        sc -= 8
    if PURPOSE.search(s):
        sc -= 6
    if CHROME.search(s):
        sc -= 10                                 # website menus / page furniture are never the law
    if DIGEST.search(s):
        sc -= 6
    if NEGATED.search(s):
        sc -= 5
    if cat == "just_cause_eviction" and re.search(r"discriminat|civil penalty|stayed by the court", s, re.I):
        sc -= 6                                  # fair-housing / penalty / court-stay text is not the just-cause rule
    if cat == "application_screening_fees" and LICENSING.search(s):
        sc -= 8                                  # licensing of brokers is not a fee cap
    if section_title and SECTION_PENALTY.search(section_title):
        sc -= 6                                  # findings / purpose / definitions sections state no obligation
    local_cite = bool(re.search(r"\((?:BMC|LAMC|SDMC)\s*[\d.]+\)", s))   # 'Prohibition of ... (BMC 13.78.016)'
    if not local_cite and (HEADING.match(s) or (not s.rstrip().endswith((".", ";", ":", ")")) and (len(s.split()) < 20 or "; " in s))):
        sc -= 4                                  # a section heading or headline, not the operative text
    if PARTY.search(s) and DEON_STRONG.search(s):
        sc += 1                                  # names the regulated party (the landlord), the operative clause
    if cat == "security_deposits" and re.search(r"\binterest (?:on|at|rate)|pay[^.]{0,40}\binterest\b|deposit interest", s, re.I):
        sc += 2                                  # for deposits, interest is the rule, not procedure
    if re.match(r"\(a\)\s", s) and DEON_STRONG.search(s) and not section_title:
        sc += 4                                  # the first subsection of a section states the core prohibition
    if inherit:
        sc += 4                                  # a list item under 'no lessor may ... the following:' is the rule itself
    return sc, hits


def key_value(s: str, cat: str) -> str | None:
    t = s.lower().replace("’", "'")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s*plus.{0,80}?(?:cost of living|consumer price|cpi).{0,80}?(\d+(?:\.\d+)?)\s*(?:percent|%)", t)
    if m:
        return f"{m.group(1)}% + CPI, max {m.group(2)}%"
    if re.search(r"one and one-half (?:times )?(?:one )?month", t):
        return "1.5 months' rent"
    m = re.search(r"\b(one|two|three|first|1|2|3)\s+month['’]?s?['’]?\s+(?:of (?:the tenant's )?)?rent", t)
    if m:
        w = {"one": "1 month's rent", "1": "1 month's rent", "first": "first month's rent",
             "two": "2 months' rent", "2": "2 months' rent", "three": "3 months' rent", "3": "3 months' rent"}
        if cat == "security_deposits" and m.group(1) == "first":
            return "1 month's rent"              # 'a security deposit equal to the first month's rent'
        return w[m.group(1)]
    m = re.search(r"security deposit interest\W{0,5}(\d+(?:\.\d+)?)\s*%", t)
    if m and cat == "security_deposits":
        return f"interest {m.group(1)}% per year"
    m = re.search(r"(\w+(?:-\w+)?)\s+(days|months)['’]?\s+notice", t)
    if m and cat == "just_cause_eviction" and not re.search(r"just cause", t):
        return f"{m.group(1)} {m.group(2)}' notice"
    m = re.search(r"(?:capped at|maximum of|no more than|by more than|more than|not (?:to )?exceed|limited to)\D{0,30}?(\d+(?:\.\d+)?)\s*(?:percent|%)", t)
    if m:
        return f"max {m.group(1)}%"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:percent|%)", t)
    if m and cat == "rent_increase_limits":
        return f"{m.group(1)}%"
    m = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", s)
    if m and cat == "application_screening_fees":
        return f"${m.group(1)}"
    if cat == "algorithmic_rent_setting":
        return "Restricts specified uses of algorithmic / coordinated rent-pricing tools (scope: see quote)"
    if cat == "just_cause_eviction" and re.search(r"just cause", t):
        return "Eviction only for a listed just cause"
    if cat == "rent_increase_limits" and re.search(r"no city or town may .{0,40}rent control", t):
        return "No local rent control allowed"
    return None


def sentence_date(s: str) -> str | None:
    for m in re.finditer(r"(?:beginning|effective|starting|on or after|as of|took effect|went into effect|takes effect)\s+(?:on\s+)?" + DATE_TXT, s, re.I):
        if re.match(r"\s*,?\s*(?:through|to|until|–|-)\s", s[m.end():m.end() + 30], re.I):
            continue                      # rate window, not the date the law took effect
        return parse_date(m.group(0))
    return None


def _norm_cite(c: str) -> str:
    c = re.sub(r"\s+", " ", c).strip()
    c = re.sub(r"^(?:Cal(?:ifornia)?\.?\s*)?Civ(?:il|\.)\s*Code\s*(?:§+|Section)\s*", "Cal. Civ. Code § ", c)
    c = re.sub(r"^Gov(?:ernment|\.)\s*Code\s*(?:§+|Section)\s*", "Cal. Gov. Code § ", c)
    c = re.sub(r"^G\.\s?L\.\s*c\.\s*(\w+),?\s*§+\s*(\w+)", r"Mass. Gen. Laws ch. \1, § \2", c)
    c = re.sub(r"^P\.L\.\s?(\d{4}),?\s*c\.\s?(\d+)", r"P.L. \1, c. \2", c)
    c = re.sub(r"^N\.J\.S\.A\.\s*", "N.J.S.A. ", c)
    c = re.sub(r"§+\s*", "§ ", c)
    return c.rstrip(".")


def extract_doc_heuristic(doc: Doc, as_of: str = config.DEFAULT_AS_OF) -> list[dict]:
    """Return candidate raw rules (LLM-compatible shape) and convert them via structure.to_record."""
    from .structure import to_record
    raws = candidates(doc, as_of)
    out = []
    for raw in raws:
        rec, why = to_record(raw, doc, as_of)
        if rec is None:
            log("rule_rejected", doc_id=doc.doc_id, reason=why, mode="heuristic")
            continue
        rec["extracted_by"] = "heuristic"
        if rec["quote_check"] == "pending_repair" or rec["quoted_span"] not in doc.raw:
            log("rule_dropped_no_quote", doc_id=doc.doc_id, mode="heuristic")
            continue
        out.append(rec)
    why = None
    if not out:
        why = ("no candidate sentence reached the score threshold in any category" if not raws
               else "candidates rejected (quote not verifiable or schema)")
    log("doc_extracted", doc_id=doc.doc_id, rules=len(out), mode="heuristic", sha256=doc.sha256,
        **({"zero_rule_reason": why} if why else {}))
    return out


MAX_PER_CAT = 2          # only for multi-topic guides that cite several statutes

CODE_ABBR = {"business and professions": "Bus. & Prof.", "civil": "Civ.", "government": "Gov.",
             "code of civil procedure": "Civ. Proc.", "health and safety": "Health & Saf."}
CA_SEC_HDR = re.compile(r"Section (\d+(?:\.\d+)?) (?:is (?:added to|amended)|of) the ([A-Z][A-Za-z ]+?) Code\b")
NJ_SEC_HDR = re.compile(r"^\s*C\.(\d+[A-Z]?:\d+[A-Z]?-[\d.]*\d[a-z]?)\b", re.M)
CITY_SEC_HDR = re.compile(r"^\s*(?:§+\s*)?(\d{1,3}\.\d{1,4}(?:\.\d{1,4})?[A-Z]?)\.?[ \t]{1,}([A-Z][^\n]{3,120})$", re.M)


def section_index(doc: Doc, prof) -> list[tuple[int, str, str]]:
    """Code-section headings inside an enacted act or ordinance: (offset, citation, heading).
    A quote under a heading is cited to that section (e.g. AB 325 -> Cal. Bus. & Prof. Code § 16729,
    P.L. 2025 c. 405 -> N.J.S.A. 46:8-18.1, Berkeley Ord. -> § 13.63.030)."""
    out = []
    if prof.kind == "enacted_act" and prof.state == "CA":
        for m in CA_SEC_HDR.finditer(doc.raw):
            abbr = CODE_ABBR.get(m.group(2).strip().lower())
            if abbr:
                out.append((m.start(), f"Cal. {abbr} Code § {m.group(1)}", ""))
    elif prof.kind == "enacted_act" and prof.state == "NJ":
        for m in NJ_SEC_HDR.finditer(doc.raw):
            line = doc.raw[m.end():doc.raw.find("\n", m.end())]
            out.append((m.start(), f"N.J.S.A. {m.group(1)}", line.strip()))
    elif prof.level == "city":
        from .profile import CITY_CODE
        code = CITY_CODE.get(prof.jurisdiction.split(",")[0], "Mun. Code")
        for m in CITY_SEC_HDR.finditer(doc.raw):
            if re.search(r"\bPage\b|\d{4}$", m.group(2)):
                continue
            out.append((m.start(), f"{code} § {m.group(1)}", m.group(2).strip()))
    return sorted(out)


def _section_at(index, pos):
    best = None
    for off, cite, title in index:
        if off > pos:
            break
        best = (cite, title)
    return best


def candidates(doc: Doc, as_of: str = config.DEFAULT_AS_OF) -> list[dict]:
    prof = profile(doc, as_of)
    m = HEADER_RE.match(doc.raw)
    bs = m.end() if m else 0
    sec_index = section_index(doc, prof)
    sents = []
    lead = None                 # last lead-in clause that ends with ':' and states an obligation
    for p in paragraphs(doc.raw, bs):
        for a, b, s in sentences(p):
            k = doc.raw.find("\n[", a, b)           # MA session-law amendment notes '[ Introductory paragraph ... ]'
            if k > a + 20:
                b = k
                s = re.sub(r"\s+", " ", doc.raw[a:b]).strip()
            if LETTER_ITEM.match(s) or (not ROMAN_ITEM.match(s) and not s.endswith(":") and not s.startswith("[")):
                lead = None
            inherit = bool(lead and ROMAN_ITEM.match(s))
            if s.endswith(":") and DEON_STRONG.search(s):
                lead = s
            if 25 <= len(s) <= 900:
                sec = _section_at(sec_index, a)
                sents.append((a, b, s, p.line_no, inherit, sec, lead if inherit else None))

    # 'Label:' on one line and its value on the next (rate tables on city pages) form one statement
    for m in re.finditer(r"^([A-Z][A-Za-z ]{3,50}):[ \t]*\n[ \t]*(\d+(?:\.\d+)?\s?%[^\n]{0,80})$", doc.raw, re.M):
        if m.start() >= bs:
            sents.append((m.start(), m.end(), re.sub(r"\s+", " ", m.group(0)).strip(),
                          doc.raw.count("\n", 0, m.start()) + 1, False, None, None))
    # a council motion: only the operative 'THEREFORE MOVE' part states what is asked for
    mv = re.search(r"THEREFORE MOVE", doc.raw)
    if mv and prof.status == "pending":
        sents = [x for x in sents if "MOVE" in x[2]] or sents
    # Pending / failed bill pages: the bill title is the rule statement.
    if prof.kind == "pending_bill":
        return _bill_candidates(doc, prof, [x[:4] for x in sents], as_of)

    meta = {x[0]: x for x in sents}
    scored = {c: [] for c in config.CATEGORIES}
    for a, b, s, ln, inherit, sec, _ in sents:
        for c in config.CATEGORIES:
            sc, hits = score_sentence(s, c, as_of, inherit, sec[1] if sec else "")
            if sc > 0:
                scored[c].append((sc, a, b, s, ln))
    doc_score = {c: sum(sorted((x[0] for x in v), reverse=True)[:3]) for c, v in scored.items()}
    top = max(doc_score.values()) if doc_score else 0
    raws = []
    for c, lst in scored.items():
        if not lst:
            continue
        lst.sort(key=lambda x: -x[0])
        best = lst[0][0]
        if best < 7 or doc_score[c] < 0.3 * top:
            continue
        # how often each explicit statute cite appears in this category's sentences (guides cite many laws)
        from collections import Counter
        cite_freq = Counter()
        for _, _, _, s2, _ in lst:
            for cm in EXPLICIT_CITE.finditer(s2):
                cite_freq[_norm_cite(cm.group(1))] += 1
        picks, seen_cites = [], set()
        if prof.kind == "guidance" and cite_freq and c not in ("rent_increase_limits", "security_deposits", "application_screening_fees"):
            # lead with the statute the guide cites most for this topic (e.g. the Anti-Eviction Act)
            top_cite, n = cite_freq.most_common(1)[0]
            if n >= 2:
                lead_c = [x for x in lst if x[0] >= 5 and (cm := EXPLICIT_CITE.search(x[3]))
                          and _norm_cite(cm.group(1)) == top_cite]
                if lead_c:
                    # prefer the sentence that states the core rule ('good cause'), then the highest score
                    sc, a, b, s, ln = max(lead_c, key=lambda x: (bool(re.search(CAT_TERMS[c][0].split("|evict")[0], x[3], re.I)), x[0]))
                    picks.append((sc, a, b, s, ln, top_cite)); seen_cites.add(top_cite)
        for sc, a, b, s, ln in lst:
            if sc < 7:
                break
            cm = EXPLICIT_CITE.search(s)
            cite = _norm_cite(cm.group(1)) if cm else None
            key = cite or prof.base_citation
            if key in seen_cites:
                continue
            # extra records per category only for statutes the guide cites repeatedly
            numeric = c in ("rent_increase_limits", "security_deposits", "application_screening_fees") and \
                bool(re.search(r"\d|month", key_value(s, c) or ""))
            local = bool(cite and re.match(r"(?:BMC|LAMC|SDMC)\s", cite))   # the city's own code section: always its own rule
            if picks and (not cite or not (cite_freq[cite] >= 2 and not picks[0][5]) and not numeric) and not local:
                continue
            seen_cites.add(key)
            picks.append((sc, a, b, s, ln, cite))
            if len(picks) >= MAX_PER_CAT or prof.kind in ("statute", "enacted_act", "ordinance"):
                break
        # value backfill: if the chosen sentence states no number, take the headline value from the
        # best-scoring sentence of the same document and category that does
        best_val = None
        if c in ("rent_increase_limits", "security_deposits", "application_screening_fees"):
            pool = [x[2] for x in sents if re.search(CAT_TERMS[c][0], x[2], re.I) and not DEFINITION.search(x[2])]
            best_val = next((key_value(s2, c) for s2 in pool
                             if key_value(s2, c) and re.search(r"\d|month", key_value(s2, c))), None)
        for sc, a, b, s, ln, cite in picks:
            sec = meta[a][5]
            own = False
            if not cite and sec and (prof.kind in ("enacted_act", "ordinance") or prof.level == "city"):
                cite, own = sec[0], True         # the code section the quote sits under
            t = _trim_glued(doc.raw[a:b], c)
            if len(t) < b - a:
                s = re.sub(r"\s+", " ", t).strip()     # drop heading lines glued in front of the rule sentence
            r = _raw(doc, prof, c, sc, s, ln, cite, as_of, own)
            if s.endswith(":") and c == "application_screening_fees" and re.search(r"in excess of the following", s, re.I):
                items = [x[2] for x in sents if x[6] and x[6][:40] == s[:40]]
                r["key_value"] = _list_value(items) or r["key_value"]
            nxt = doc.raw[b:b + 400]
            if r["key_value"] and "$" in r["key_value"] and re.search(r"Consumer Price Index", nxt, re.I):
                r["key_value"] += ", CPI-adjusted annually"
                r["notes"] += "; the next sentence adjusts the amount by the Consumer Price Index"
            if best_val and not (r["key_value"] and re.search(r"\d|month", r["key_value"])) and not cite \
                    and "current rate not in corpus" not in r["notes"]:
                r["key_value"] = best_val
                r["notes"] += "; key value taken from another sentence of the same section"
            raws.append(r)
    # Completeness for single-section statutes: the organisers chose each section for a reason, so a statute
    # that produced no rule yields its strongest obligation sentence in its best-matching category.
    if prof.kind == "statute" and not raws:
        best = None
        for a, b, s, ln, inherit, sec, _ in sents:
            if not (DEON_STRONG.search(s) or DEON_WEAK.search(s)) or DEFINITION.search(s) or PURPOSE.search(s) \
                    or not s.rstrip().endswith((".", ";", ":")):
                continue                         # headings (no closing punctuation) are not the obligation
            for c in config.CATEGORIES:
                strong, weak = CAT_TERMS[c]
                h = 2 * len(re.findall(strong, s, re.I)) + len(re.findall(weak, s, re.I))
                if h and (best is None or h > best[0]):
                    best = (h, c, a, b, s, ln)
        if best:
            h, c, a, b, s, ln = best
            if not s.rstrip().endswith((".", ";", ":")):          # sentence split inside an abbreviation
                j = doc.raw.find(".", b)
                if 0 < j - a <= 900:
                    s = re.sub(r"\s+", " ", doc.raw[a:j + 1]).strip()
            r = _raw(doc, prof, c, 7, s, ln, None, as_of, True)
            r["notes"] += "; statute-completeness fallback (each corpus statute section yields its main obligation)"
            raws.append(r)
            return raws
    # Fallback for laws whose operative clause uses a defined term instead of the topic
    # word (e.g. the FAIR Act's 'coordinator'): if the document's title is clearly about a
    # category but no sentence qualified, take its strongest prohibition clause.
    head = doc.raw[bs:bs + 1500]
    got = {r["category"] for r in raws}
    for c in config.CATEGORIES:
        if c in got or not re.search(CAT_TERMS[c][0], head, re.I):
            continue
        if prof.kind not in ("statute", "enacted_act") or got:
            continue
        best = None
        for a, b, s, ln, *_ in sents:
            if not re.search(r"unlawful|prohibit|shall not|may not", s, re.I) or DEFINITION.search(s) or PURPOSE.search(s):
                continue
            sc = 3 * len(re.findall(r"unlawful|prohibit", s, re.I)) + (2 if VALUE.search(s) else 0) - len(PROCEDURAL.findall(s))
            if best is None or sc > best[0]:
                best = (sc, a, b, s, ln)
        if best:
            sc, a, b, s, ln = best
            span = doc.raw[a:b]                  # the whole clause, never cut mid-sentence
            if len(span) > 900 and ";" in span[150:900]:
                span = span[:span.rfind(";", 0, 900) + 1]
            if span.rstrip()[-1:] not in (".", ";"):        # sentence split inside a citation: run to the clause end
                j = doc.raw.find(";", b)
                if 0 < j - a <= 900:
                    span = doc.raw[a:j + 1]
            sec = meta[a][5]
            r = _raw(doc, prof, c, 7, re.sub(r"\s+", " ", span), ln, sec[0] if sec else None, as_of, True)
            r["quoted_span"] = span.strip()
            r["notes"] += "; title-topic fallback (operative clause uses a defined term)"
            raws.append(r)
    return raws


def _list_value(items: list[str]) -> str | None:
    """'no lessor may require ... in excess of the following: (i) ... (ii) ...' -> the allowed charges."""
    names = []
    for it in items:
        t = it.lower()
        for pat, name in ((r"security deposit", "security deposit"), (r"first (?:full )?month", "first month's rent"),
                          (r"last (?:full )?month", "last month's rent"), (r"\block\b|\bkey\b", "lock and key cost")):
            if re.search(pat, t) and name not in names:
                names.append(name)
                break
    return ("Only these up-front charges: " + ", ".join(names)) if len(names) >= 2 else None


def _trim_glued(s: str, cat: str) -> str:
    """A sentence glued to the heading lines before it (no period on web pages): start at the first line
    that carries the topic term. The result is still an exact substring of the source."""
    if "\n" not in s:
        return s
    strong = CAT_TERMS[cat][0]
    lines = s.split("\n")
    if re.search(strong, lines[0], re.I) and DEON_STRONG.search(lines[0]):
        return s
    pos = 0
    for i, ln_ in enumerate(lines):
        prev = lines[i - 1].rstrip() if i else ""
        joins = re.search(r"(?:,|\b(?:or|and|of|the|a|an|to|for|by|with|in|thereof|any|be|is|are))$", prev, re.I)
        if i and ln_.strip()[:1].isupper() and not joins and re.search(strong, ln_, re.I) \
                and (DEON_STRONG.search(ln_) or VALUE.search(ln_)):
            return s[pos:].strip()
        pos += len(ln_) + 1
    return s


def _raw(doc, prof, cat, sc, s, ln, cite, as_of: str = config.DEFAULT_AS_OF, own_section: bool = False):
    juris, level = prof.jurisdiction, prof.level
    citation = cite or prof.base_citation
    if cite and STATE_CITE.search(cite) and prof.level == "city":
        juris, level = prof.state, "state"
    if cite and re.match(r"(?:BMC|LAMC|SDMC)\s*", cite) and prof.level == "city":
        from .profile import CITY_CODE
        citation = f"{CITY_CODE.get(prof.jurisdiction.split(',')[0], 'Mun. Code')} § {re.sub(r'^(?:BMC|LAMC|SDMC)\s*', '', cite)}"
    if cite and cite.startswith("§") and prof.level == "city":
        from .profile import CITY_CODE
        citation = f"{CITY_CODE.get(prof.jurisdiction.split(',')[0], 'Mun. Code')} {cite}"
    if prof.level == "city" and re.match(r"^[A-Z][A-Za-z ]{3,50}: \d", s) and not cite:
        from .profile import CITY_CODE
        citation = CITY_CODE.get(prof.jurisdiction.split(",")[0], citation)   # a rates table names no section
    eff = sentence_date(s) if prof.kind != "pending_bill" else None
    if not eff and (not cite or own_section):
        eff = prof.effective_date               # a section of this same act/ordinance shares its dates
    status = prof.status
    title_src = citation if len(citation) < 60 else prof.base_citation
    if cite and prof.kind == "enacted_act" and prof.base_citation not in citation:
        citation = f"{citation} ({prof.base_citation})"     # codified section + the act that added it
    kv = key_value(s, cat)
    notes = f"heuristic score {sc}; doc kind {prof.kind}"
    m_end = re.search(r"through\s+" + DATE_TXT, s, re.I)
    if kv and m_end and (parse_date(m_end.group(0)) or "9999") < as_of:
        notes += f"; the stated value applied only until {parse_date(m_end.group(0))} - current rate not in corpus"
        kv = None
    return {
        "jurisdiction": juris, "level": level, "category": cat, "status": status,
        "title": f"{LABEL[cat]} ({title_src})",
        "requirement": s if len(s) <= 400 else s[:397] + "...",
        "key_value": kv,
        "coverage_text": "", "exemptions": None,
        "coverage": {"all": [], "exempt_any": [], "defers_to_local": False},
        "effective_date": eff, "enacted_date": prof.enacted_date,
        "citation": citation, "quoted_span": s, "quote_line_start": ln, "quote_line_end": ln,
        "penalty": None, "confidence": round(min(0.9, 0.35 + sc / 30), 2),
        "notes": notes,
        # statutes: date the current text version took effect (kept apart from effective_date, which the
        # official template leaves null for long-standing statutes)
        "current_version_effective": prof.version_date if prof.kind == "statute" else None,
    }


def _bill_candidates(doc, prof, sents, as_of: str = config.DEFAULT_AS_OF):
    raws = []
    title = next((x for x in sents if re.match(r"An Act\b", x[2])), None)
    if not title:
        return raws
    a, b, s, ln = title
    s = re.sub(r"\s*View Text\b.*$", "", s, flags=re.S).strip()      # page furniture after the bill title
    status = "pending"
    sess = re.search(r"\((\d+)(?:st|nd|rd|th) Gen\. Court\)", prof.base_citation)
    # the Massachusetts General Court sits in two-year sessions: 2025-2026 is the 194th
    current = 194 + (int(as_of[:4]) - 2025) // 2
    if sess and int(sess.group(1)) < current:      # session already ended without enactment
        status = "failed"
    for c in config.CATEGORIES:
        strong, weak = CAT_TERMS[c]
        if re.search(strong, s, re.I) or (c == "rent_increase_limits" and re.search(r"rent stabiliz", s, re.I)):
            r = _raw(doc, prof, c, 10, s, ln, None, as_of)
            r["status"] = status
            r["effective_date"] = None
            r["requirement"] = f"Proposed: {s}. This is a bill, not law."
            r["key_value"] = None
            raws.append(r)
    return raws
