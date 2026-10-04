"""Validation against the OFFICIAL rule_record.schema.json from the starter pack."""
from __future__ import annotations
import json
from functools import lru_cache

from jsonschema import Draft202012Validator

from . import config


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    schema = json.loads(config.SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def errors(record: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in validator().iter_errors(record)]


def is_valid(record: dict) -> bool:
    return not errors(record)


LOOKUP_RESULTS = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}
