"""LLM-powered semantic validation — catches what regexes can't.

Examples: a phone number that is valid-looking but repeated digits (9999999999),
a city name in the state column, gibberish names, price outliers vs. siblings.

Gracefully returns None when no ANTHROPIC_API_KEY is configured; callers fall
back to rule-based validation only.
"""

from __future__ import annotations

import json
import os

CHUNK = 40

SYSTEM = """You are a data-quality reviewer for batch API uploads.
You receive rows as JSON with their 1-based row numbers.
Flag ONLY real problems a human data steward would stop: placeholder/garbage values
(e.g. 'test', 'asdf', '9999999999'), values in the wrong column, impossible values
(negative quantities, future birth dates), inconsistent formats within a column,
and likely typos in categorical values. Do NOT flag stylistic differences.
Respond with ONLY a JSON array (no prose):
[{"row": <int>, "field": "<column>", "message": "<short reason>", "severity": "warning"|"error"}]
Return [] if everything looks fine."""


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def validate_with_ai(rows: list[dict], field_names: list[str]) -> list[list[dict]] | None:
    """Returns per-row issue lists (aligned with rows), or None if AI unavailable."""
    if not ai_available():
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()
    model = os.environ.get("BATCHPILOT_AI_MODEL", "claude-haiku-4-5-20251001")
    issues: list[list[dict]] = [[] for _ in rows]

    for start in range(0, len(rows), CHUNK):
        chunk = rows[start:start + CHUNK]
        payload = [{"row": start + i + 1, **{k: _s(v) for k, v in r.items()}}
                   for i, r in enumerate(chunk)]
        prompt = (f"Expected columns: {json.dumps(field_names)}\n"
                  f"Rows:\n{json.dumps(payload, ensure_ascii=False)}")
        try:
            resp = client.messages.create(
                model=model, max_tokens=2000, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("json").strip()
            found = json.loads(text)
        except Exception:
            continue  # AI issues must never block the pipeline

        for item in found if isinstance(found, list) else []:
            try:
                idx = int(item["row"]) - 1
                if 0 <= idx < len(rows):
                    issues[idx].append({
                        "field": str(item.get("field", "")),
                        "code": "ai",
                        "message": str(item.get("message", "flagged by AI")),
                        "severity": item.get("severity", "warning"),
                    })
            except (KeyError, TypeError, ValueError):
                continue
    return issues


def _s(v):
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)
