# CiteMap — Rental Housing Law Navigator

> **Which housing rules apply to this apartment today — and what is about to change?**
> Address-level answers for U.S. rental housing law. Every answer quotes the exact law it comes from.

Built solo in 24 hours for the **Hack-Nation 7th Global AI Hackathon · Challenge 02 · RealPage** (October 2026).

> ⚠️ **Information only — not legal advice.** CiteMap makes public law visible. It is not a compliance certification. Always check the cited source.

| | |
|---|---|
| Live demo | https://cite-map.vercel.app |
| Rule records | **51**: 44 extracted automatically from 54 law texts (incl. every single-section statute in the corpus, Massachusetts notice / reprisal statutes, SF and Berkeley deposit rules) + 7 labelled "text not in corpus" records: 1 ordinance named on a city's own page (Jersey City Rent Control Ordinance, Chapter 260), 4 found from the organisers' link-only source list (topic read from the URL), 2 named only in the change cases (Hoboken ban, MA ballot question IP 25-21). These always answer **unknown** (or failed), never applies. Each record has `record_type` (operative law / pending bill / failed proposal / placeholder) and `source_authority` |
| Rule inventory | `outputs/rule_inventory.json`: every jurisdiction × category (78 cells) with status — rule found, possible gap (with candidate sentences for human review), no local rule because state law bars it (quoted), text not in corpus, or no rule found in corpus. This is how we represent "no rule at this level" findings |
| Quotes | Every extracted rule's quote is an exact substring of its source file (rules whose quote can't be found are dropped). Placeholders say "text not in corpus" instead of quoting law. |
| Change tests | T1, T3, T4, T5 produce the expected address sets from extracted rules; T2 (Hoboken / Jersey City bans) uses placeholder records because those ordinances are link-only — reported as **unknown**, never as confirmed law |
| Automated tests | **86 passing** (`make test`) |
| Randomized attack tests | `make stress` runs rounds of ~100 / 250 / 1,000 randomized tests (API fuzzing, concurrency, broken documents, bad data, random UI clicking); target < 1 % failures per round. Latest run (4 Oct, 06:20): 110 → 0, 275 → 0, 1,100 → 0 failed — after fixing what the first run found (placeholder explanations missing their citation, plus two test-harness bugs). |

**How this was built:** solo, with AI coding assistance (Claude) for writing and reviewing code. The submitted system itself uses **no AI**: extraction is rule-based code, decisions are plain code.

---

## What it does
For each of ~500 multifamily buildings in **California, New Jersey and Massachusetts**, CiteMap:

1. resolves the **legal** jurisdiction (state › county › city — “Dorchester” is Boston, “San Ysidro” is San Diego);
2. lists every rule in 6 categories — rent increase limits, just-cause eviction, security deposits, application/screening fees, screening restrictions, algorithmic rent-setting;
3. gives each a result: `applies` · `unknown` · `superseded` · `not_yet_effective` · `pending`;
4. explains it in plain **English and Spanish**, with the **citation**, the **exact quoted text**, the **source URL** and **retrieval date**;
5. answers **as of any date**, and shows which addresses a **new or pending law** would affect, with **conflict flags** for human review.

## How it works
**Rule-based code extracts the rules. Plain code decides. Every answer points to the source.**

```
 law texts ─► PROFILE ─► FIND RULES ─► COVERAGE ─► VERIFY ─► RECONCILE ─► rules.json
             (kind,      (sentence     (cutoff     (official  (drop noise,
              citation,   scoring,      dates,      schema,    merge, link
              status,     key values)   units,      exact      precedence,
              dates)                    exemptions) quote)     flag conflicts)
 500 addresses ─► RESOLVE legal city ─► FACTS (year, unit ranges; gaps = unknown)
                                   │
            rules.json ──► DECIDE (three-valued logic, no AI) ──► lookups.json
                                   │
         change tests / new law ──► TRACK (as-of dates, diffs) ──► changes.json
                                   │
         FastAPI + SQLite  ──► web app (live any-date queries, optional login, static fallback)
```

### Module A — extraction (automated, no hand-coded rules)
| Step | File | What it does |
|---|---|---|
| Profile | `citemap/extract/profile.py` | Document kind (statute / enacted act / pending bill / ordinance / guidance), **citation derived from the URL or text** (`Cal. Civ. Code § 1947.12`, `Mass. Gen. Laws ch. 186, § 15B`, `P.L. 2026, c. 43`), enactment + effective dates (incl. relative rules like *“first day of the twelfth month following enactment”*) |
| Find rules | `citemap/extract/heuristic.py` | Scores every sentence per category (topic words × obligation words × numbers − procedural/definition/purpose language), picks the best quote, extracts the key value (`5% + CPI, max 10%`, `1.5 months' rent`, `$50`) |
| Coverage | `citemap/extract/coverage.py` | Reads machine-testable conditions with their evidence: certificate-of-occupancy cutoffs (SF 1979-06-13, LA 1978-10-01), built-before years, 15-year rolling exemptions, unit thresholds, owner-occupied / small-landlord exemptions, *defers to stricter local law* |
| Verify | `citemap/extract/verify.py`, `schema.py` | Official JSON Schema check; quote must be an **exact substring** of the corpus file |
| Reconcile | `citemap/extract/reconcile.py` | Generic, logged clean-up rules; precedence links; conflict flags from the law's own preemption language (only for cities that have a local rule of that kind); penalty / exemption text where the source states it; placeholders for change-case laws whose text is not in the corpus |
| Optional AI | `citemap/extract/llm.py`, `prompts.py`, `structure.py` | Same pipeline with an LLM extractor if `ANTHROPIC_API_KEY` is set; falls back to the rule-based extractor on any failure |

### Module B — address lookup
`citemap/resolve/jurisdiction.py` · `citemap/apply/facts.py` · `citemap/apply/engine.py` · `citemap/apply/explain.py`
- **Three-valued coverage:** true / false / **unknown**. A missing fact is never guessed.
- **Unit ranges from use codes** (`APT 7-30 UNITS`, NJ class 4C = 5+ units) turn many unknowns into real answers.
- **Certificate-of-occupancy cutoffs:** a building built in the cutoff year is `unknown` (the guide's rule).
- **Precedence:** where a stricter local rule applies, the deferring state rule is `superseded`.

### Module C — change tracking
`citemap/change/track.py` — maps the test cases to our rules by jurisdiction + category, compares results across dates, flags conflicts, and computes the impact of any new law file.

| Test | Result |
|---|---|
| T1 CA AB 325 | 250 addresses = every CA address (not yet effective 2025-12-31 → applies 2026-01-02) |
| T2 Hoboken / Jersey City bans | 90 addresses = Hoboken + Jersey City only, never Newark (placeholder records, result **unknown**: ordinance text not in corpus) |
| T3 NJ FAIR Act | 140 NJ addresses; only the 90 Jersey City + Hoboken addresses flagged for possible preemption (Newark: no flag) |
| T4 MA S.2983 / H.5222 | 110 MA addresses, reported as **pending**, never in force |
| T5 MA ballot question (struck) | **empty set** — no rent cap reported for Boston or Cambridge |

## Deploy on Vercel
`api/index.py` + `vercel.json` run the full FastAPI app as a Vercel function (the database lives in `/tmp`, so demo accounts reset when Vercel restarts the function). Import the repo on vercel.com, Framework "Other", no build command. Health check: `/api/health`.

## Adding a city
Edit `data/jurisdictions.json` (states, cities, city code names, postal-name aliases), add the documents, run `make all`. New citation formats or coverage phrasings still need a new pattern.

## Quick start
All commands run **on your machine, inside the repo folder** (e.g. `~/hacknation/citemap`).

```bash
# On your Mac, in ~/hacknation/citemap
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt      # pinned versions (runtime + tests)
make all        # rules.json -> lookups.json -> changes.json (+ copies to web/data)
make test       # 86 tests
uvicorn server.app:app --port 8000     # open http://localhost:8000
```

**A new law (e.g. the hour-16 ordinance):**
```bash
# On your Mac, in ~/hacknation/citemap
python3 -m citemap.cli add-law path/to/ordinance.txt --jurisdiction "Cambridge, MA"
```
It is extracted unaided, added to `rules.json`, all lookups are recomputed, `changes.json` gets a `T6` entry and `outputs/new_law_report.json` lists before/after results.

**Other commands:** `python3 -m citemap.cli lookup A0001 --as-of 2027-07-02` · `python3 -m citemap.cli stats` · `python3 -m citemap.cli clean-new-laws`

**Link-only sources** (33 of 87 had no text): `python3 tools/fetch_links.py` downloads each page once (polite, logged) into `data/supplement/`, **skipping sources marked `check-terms`**; `make all` then includes them automatically.

**Important workflow note (hour-16 ordinance):** after `add-law`, copy the final `outputs/` before doing anything else. `clean-new-laws` removes added laws on purpose. The test suite snapshots and restores `outputs/`, `web/data/` and `data/new_laws/`, so `make test` no longer erases T6, but copying first is still the safe order.

## Outputs (submission files)
| File | Contents |
|---|---|
| `outputs/rules.json` | `{"rules": [...]}` — official schema + extra audit fields (retrieval date, evidence, extractor, confidence) |
| `outputs/lookups.json` | `{"as_of": "2026-10-01", "lookups": {address_id: [{team_rule_id, result, explanation, conflict_flag}]}}` — all 500 addresses |
| `outputs/changes.json` | `{test_id: {affected_address_ids, conflict_flag_address_ids, notes}}` |
| `outputs/lookups_detail.json` | UI file: jurisdiction stack, facts, Spanish explanations, missing facts |
| `audit/audit.jsonl` | Written locally each time the pipeline runs (not committed): every extraction with the source file's hash, zero-rule reasons, drops, merges, placeholders, and the hash of the rules.json it produced |

## Web app
`server/app.py` (FastAPI) + `server/db.py` (SQLite) + `web/` (no build step).
- **Lookup:** search, jurisdiction breadcrumb, building facts (gaps shown), any-date slider, rule cards with status chips, “Show the law” (exact quote + source + retrieval date), “Why this answer?” (coverage, evidence, missing facts), EN/ES.
- **What's changing:** T1–T6 with affected addresses per city and conflict flags.
- **Rule library:** filterable table of every extracted rule.
- **How it works:** pipeline, trade-offs, what it never does, live audit log.
- **Accounts (optional):** register / sign in (PBKDF2-hashed passwords; HTTP-only, SameSite=Strict, Secure-over-HTTPS session cookie; only a hash of the session token is stored; sessions expire after 7 days; per-IP rate limit on login/register; generic error messages), roles (renter / advocate / housing provider), saved addresses. **Everything works as a guest**, and guest lookups are not logged.
- **Fail-open:** if the server is unreachable the app switches to *offline mode* and serves the saved answers from `web/data/`.

Deploy: `Dockerfile` (any container host), `render.yaml` (Render), `Procfile` (Railway/Replit). The `web/` folder alone also works as a static site (offline mode).

## Design decisions & trade-offs
| We chose | Over | Because | What we gave up |
|---|---|---|---|
| Rule-based automated extraction (AI optional) | LLM-only extraction | Runs offline, free, reproducible; every pattern is inspectable | Lower recall on messy web pages |
| No AI at answer time | Chat over the law | Same input → same answer; cannot invent law | Free-form questions |
| **“Unknown”** with the missing fact named | Best guess | Honest; a renter can act on it | Some answers are less complete |
| Exact-quote verification | Trusting extracted text | Every citation checkable in seconds | A few rules dropped |
| Static fallback + SQLite | Cloud database | Demo cannot fail mid-judging | No multi-server scaling (not needed) |

## Responsible AI
**Does:** cite source + retrieval date for every rule · show an “as of” date on every answer · separate enacted, pending and failed law · say unknown instead of guessing · flag conflicts for human review · keep an audit log · explain in plain language (EN/ES).
**Never:** legal advice or compliance certification · ways to avoid a rule · invented rules or citations · non-public data · scraping against site terms.

## Scaling to new jurisdictions
For a city we already cover: add the document (or `add-law`) and rerun — citations, dates, coverage and impact are derived automatically, as the hour-16 workflow shows. A **new** city is a data entry in `data/jurisdictions.json`; new citation formats or coverage phrasings need a new pattern.

## Method note (one page)
1. **Profile** each document: kind (statute / enacted act / pending bill / ordinance / guidance), official citation from the URL pattern, enacted and effective dates (explicit or relative, e.g. "first day of the twelfth month following enactment"; rate windows are never taken as law dates).
2. **Find rules**: score each sentence per category (topic words × obligation words × values − procedural, definition, purpose, heading, menu and bill-summary penalties). List items inherit the obligation of their lead-in ("no lessor may require … the following:"). A quote inside a code section is cited to that section (e.g. AB 325 → Cal. Bus. & Prof. Code § 16729; P.L. 2025 c. 405 → N.J.S.A. 46:8-18.1).
3. **Coverage**: 13 phrase patterns (certificate-of-occupancy cutoffs, built-before years, unit thresholds, owner-occupied and small-landlord exemptions, "defers to stricter local law").
4. **Verify**: official JSON schema; the quote must be an exact substring of the source.
5. **Reconcile**: drop fragments and noise, merge duplicates, flag conflicts for human review, mark drafts whose adoption date is blank.
6. **Inventory**: a jurisdiction × category matrix flags gaps and records negative findings with their basis.
7. **Decide** per address with three-valued logic; a missing fact gives **unknown** with the fact named; expired rate windows give no value ("current rate not in corpus").
8. **Track** changes by re-deciding on two dates and diffing.

## Known limitations (honest list)
- Newark and Hoboken ordinances are link-only **and** marked `check-terms` (not cleared for download), so their rules cannot be extracted by anyone from this corpus; we report them as unknown only where a source names the topic.
- 33 of 87 sources were link-only. The Hoboken / Jersey City bans and the Massachusetts ballot question (IP 25-21) are therefore **placeholder records** built from the change-case file and the link-only list: they are labelled "text not in corpus", give **unknown** (or **failed** for IP 25-21), and quote the change-case title, not law. `tools/fetch_links.py` can fetch the allowed pages.
- Some city citations stop at the code level (Berkeley rent and just cause, Santa Ana, SF rent: `S.F. Admin. Code`) when the page does not name a section.
- The NJ just-cause record quotes a notice rule from the DCA guide, cited to N.J.S.A. 2A:18-61.1; the guide's clearer "good cause" sentence cites a different section.
- Berkeley's 5% is the cap on the annual adjustment, not the 2026 adjustment itself; LA's 3% rate window ended 2026-06-30, so LA shows no current rate.
- San Diego's algorithmic ban comes from a staff report whose adoption certificate is blank, so it carries a review flag.
- Conflict flags also mark a local rule in a category that state law bars (e.g. a Cambridge rent cap vs. Mass. Gen. Laws ch. 40P).
- Change-test files are used only to name which of our rules each test refers to (required by the output format); affected and conflict address sets come from the engine's own answers. Exception: the Hoboken ban placeholder and IP 25-21, which are named only in the change cases.
- "No rule at this level" findings (23, each with the documents searched and any rejected candidates with a reason such as `cross_jurisdiction_reference`; only for jurisdictions whose corpus text we searched; never for link-only Newark/Hoboken) are written to `rules.json` under a separate top-level key `no_rule_findings` (the `rules` list keeps the template format) and to `outputs/rule_inventory.json`.
- Statutes keep `effective_date: null` (as in the official template); the date their current text version took effect is in `current_version_effective`.
- The "confidence" number is a heuristic extraction score, not a probability; the app labels it that way and shows a step-by-step decision trace for every answer.
- Owner type is never in the public data, so owner-based exemptions stay unknown unless the unit count rules them out.
- There is no official scoring script in our pack; our numbers come from our own tests built from the brief. The answer key and CiteMap have not been reviewed by counsel.

## Sources & credits
RealPage × Hack-Nation starter pack (public statutes, ordinances, bills; public assessor data). No customer, resident or pricing data.
Built by **Arjun Singh Bhadoria** (University of Zurich), with AI coding assistance (Claude).
