"""Thin, cached wrapper around the Anthropic Messages API.

* The key is read from the environment (.env loaded by config.load_env); it is never logged.
* Every response is cached on disk keyed by (model, prompt hash), so reruns are free and
  reproducible, and the hour-16 rerun only pays for the new document.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time

from .. import config
from ..audit import log


class LLMUnavailable(RuntimeError):
    pass


def _client():
    config.load_env()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    import anthropic
    # Always talk to the public API directly (ignore any ANTHROPIC_BASE_URL in the shell).
    return anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=4,
                               timeout=300)


def _cache_path(key: str):
    d = config.CACHE / "llm"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def complete_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 16000,
                  tag: str = "") -> dict | list:
    """Ask the model for JSON; returns the parsed object. Cached."""
    model = model or config.LLM_MODEL
    h = hashlib.sha256(json.dumps([model, system, user]).encode()).hexdigest()[:24]
    cp = _cache_path(h)
    if cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))["parsed"]
    client = _client()
    t0 = time.time()
    last_err = None
    for attempt in range(3):
        try:
            text_parts = []
            with client.messages.stream(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                for ev in stream.text_stream:
                    text_parts.append(ev)
                final = stream.get_final_message()
            text = "".join(text_parts)
            parsed = parse_json(text)
            usage = getattr(final, "usage", None)
            log("llm_call", tag=tag, model=model, prompt_hash=h, seconds=round(time.time() - t0, 1),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None))
            cp.write_text(json.dumps({"model": model, "tag": tag, "raw": text, "parsed": parsed}), encoding="utf-8")
            return parsed
        except LLMUnavailable:
            raise
        except Exception as e:  # network / parse errors: retry, then give up
            last_err = e
            log("llm_error", tag=tag, model=model, error=str(e)[:300], attempt=attempt)
            time.sleep(3 * (attempt + 1))
    raise LLMUnavailable(f"LLM failed after retries: {last_err}")


def parse_json(text: str):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # take the outermost {...} or [...]
        starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
        if not starts:
            raise
        s = min(starts)
        e = max(t.rfind("}"), t.rfind("]"))
        return json.loads(t[s:e + 1])
