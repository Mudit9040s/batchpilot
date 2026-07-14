"""Deterministic, offline validation engine driven by profile field rules."""

from __future__ import annotations

import re
from datetime import date, datetime

from .config import Profile

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-()]{6,17}$")


def _is_number(v) -> bool:
    try:
        float(str(v).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def _is_integer(v) -> bool:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) == int(float(s))
    except (TypeError, ValueError):
        return False


def _is_date(v) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            datetime.strptime(str(v).strip(), fmt)
            return True
        except ValueError:
            continue
    return False


def validate_rows(profile: Profile, rows: list[dict]) -> list[list[dict]]:
    """Returns per-row list of issues: {field, code, message, severity}."""
    issues: list[list[dict]] = [[] for _ in rows]
    seen: dict[str, dict] = {f.name: {} for f in profile.fields if f.unique}

    for i, row in enumerate(rows):
        for f in profile.fields:
            v = row.get(f.name)
            if v is None or str(v).strip() == "":
                if f.required:
                    issues[i].append({"field": f.name, "code": "missing",
                                      "message": f"'{f.name}' is required", "severity": "error"})
                continue

            sv = str(v).strip()
            if f.type == "integer" and not _is_integer(v):
                issues[i].append({"field": f.name, "code": "type",
                                  "message": f"'{f.name}' must be a whole number (got '{sv}')",
                                  "severity": "error"})
            elif f.type == "number" and not _is_number(v):
                issues[i].append({"field": f.name, "code": "type",
                                  "message": f"'{f.name}' must be numeric (got '{sv}')",
                                  "severity": "error"})
            elif f.type == "email" and not EMAIL_RE.match(sv):
                issues[i].append({"field": f.name, "code": "format",
                                  "message": f"'{f.name}' is not a valid email ('{sv}')",
                                  "severity": "error"})
            elif f.type == "phone" and not PHONE_RE.match(sv):
                issues[i].append({"field": f.name, "code": "format",
                                  "message": f"'{f.name}' is not a valid phone number ('{sv}')",
                                  "severity": "error"})
            elif f.type == "date" and not _is_date(v):
                issues[i].append({"field": f.name, "code": "format",
                                  "message": f"'{f.name}' is not a recognizable date ('{sv}')",
                                  "severity": "error"})

            if f.regex and not re.fullmatch(f.regex, sv):
                issues[i].append({"field": f.name, "code": "regex",
                                  "message": f"'{f.name}' does not match pattern {f.regex}",
                                  "severity": "error"})
            if f.max_length and len(sv) > f.max_length:
                issues[i].append({"field": f.name, "code": "length",
                                  "message": f"'{f.name}' exceeds {f.max_length} characters",
                                  "severity": "warning"})
            if f.min is not None and _is_number(v) and float(str(v).replace(",", "")) < f.min:
                issues[i].append({"field": f.name, "code": "range",
                                  "message": f"'{f.name}' below minimum {f.min}", "severity": "error"})
            if f.max is not None and _is_number(v) and float(str(v).replace(",", "")) > f.max:
                issues[i].append({"field": f.name, "code": "range",
                                  "message": f"'{f.name}' above maximum {f.max}", "severity": "error"})

            if f.unique:
                first = seen[f.name].get(sv)
                if first is not None:
                    issues[i].append({"field": f.name, "code": "duplicate",
                                      "message": f"duplicate '{f.name}' (same as row {first + 1})",
                                      "severity": "warning"})
                else:
                    seen[f.name][sv] = i

    return issues
