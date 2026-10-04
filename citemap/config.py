"""Central settings for CiteMap. Paths are relative to the repo root."""
from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parent.parent
STARTER = Path(os.environ.get("CITEMAP_STARTER", ROOT / "starter"))
CORPUS_DIR = STARTER / "corpus"
MANIFEST = CORPUS_DIR / "corpus_manifest.csv"
SUPPLEMENT_DIR = ROOT / "data" / "supplement"        # pages fetched in the morning by tools/fetch_links.py
NEW_LAWS_DIR = ROOT / "data" / "new_laws"            # hour-16 style new documents dropped here
ADDRESSES = STARTER / "data" / "sample_addresses.csv"
GEOCODED = ROOT / "data" / "geocoded.csv"            # optional, from tools/geocode_census.py
SCHEMA = STARTER / "schema" / "rule_record.schema.json"
CHANGE_TESTS = STARTER / "dev" / "change_tests.json"

OUTPUTS = ROOT / "outputs"
CACHE = ROOT / "cache"
AUDIT = ROOT / "audit"

DEFAULT_AS_OF = "2026-10-01"

# Jurisdictions are data, not code: data/jurisdictions.json (states, cities, city code names, aliases).
JURISDICTIONS_FILE = ROOT / "data" / "jurisdictions.json"
_J = json.loads(JURISDICTIONS_FILE.read_text(encoding="utf-8"))
STATES = _J["states"]
CITIES = _J["cities"]
CITY_CODES = _J.get("city_codes", {})
ALIASES = {tuple(k.split("|", 1)): v for k, v in _J.get("aliases", {}).items()}
CATEGORIES = [
    "rent_increase_limits",
    "just_cause_eviction",
    "security_deposits",
    "application_screening_fees",
    "screening_restrictions",
    "algorithmic_rent_setting",
]

LLM_MODEL = os.environ.get("CITEMAP_MODEL", "claude-sonnet-5-5")


def load_env():
    """Load the API key from .env / citemap.env without ever printing it."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for name in (".env", "citemap.env"):
        p = ROOT / name
        if p.exists():
            load_dotenv(p, override=False)


def has_llm() -> bool:
    load_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("CITEMAP_NO_LLM") != "1"
