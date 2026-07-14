"""Profile loading. A profile is a YAML file describing a target API + field rules.

Secrets are referenced as ${ENV_VAR} and substituted at load time, so profiles
are safe to commit to a public repo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")

# Sensible default so the bundled demo profile works out of the box.
os.environ.setdefault("BATCHPILOT_BASE_URL",
                      f"http://127.0.0.1:{os.environ.get('PORT', '8000')}")

PROFILES_DIR = Path(os.environ.get("BATCHPILOT_PROFILES_DIR", "profiles"))


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


@dataclass
class FieldRule:
    name: str
    required: bool = False
    type: str = "string"  # string | integer | number | email | phone | date
    regex: str | None = None
    min: float | None = None
    max: float | None = None
    unique: bool = False
    max_length: int | None = None


@dataclass
class ResponseMap:
    """How to read per-row outcomes out of a partial-acceptance response."""

    results_path: str = "results"        # dot-path to the list of per-record results
    status_field: str = "status"         # field inside each result holding the outcome
    success_values: list = field(default_factory=lambda: ["success", "ok", "accepted"])
    message_field: str = "message"       # field with the human-readable reason
    index_field: str | None = None       # optional field carrying the record index
    # Value-based matching (e.g. FieldAssist keys results by ERPId, not index):
    match_field: str | None = None       # field in each RESULT item (e.g. "ERPId")
    record_field: str | None = None      # field in each SENT record (e.g. "OutletErpId")
    # What to assume for rows absent from the results list. FieldAssist's
    # ResponseList contains only errors, so absent = accepted → "success".
    missing_means: str = "unknown"       # unknown | success | failed


@dataclass
class Profile:
    key: str
    name: str
    endpoint: str
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    records_key: str = "records"         # request body: {records_key: [...]}
    batch_size: int = 50
    max_retries: int = 3
    timeout: float = 30.0
    fields: list[FieldRule] = field(default_factory=list)
    response_map: ResponseMap = field(default_factory=ResponseMap)
    description: str = ""

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


def load_profile(path: Path) -> Profile:
    raw = yaml.safe_load(path.read_text())
    raw = _substitute_env(raw)
    fields = [FieldRule(**f) for f in raw.get("fields", [])]
    rm = ResponseMap(**raw.get("response_map", {}))
    headers = dict(raw.get("headers", {}))
    # Optional friendly auth block:  auth: {type: basic, username: ..., password: ...}
    auth = raw.get("auth") or {}
    if str(auth.get("type", "")).lower() == "basic" and auth.get("username"):
        import base64
        cred = base64.b64encode(
            f"{auth['username']}:{auth.get('password', '')}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    return Profile(
        key=path.stem,
        name=raw.get("name", path.stem),
        endpoint=raw["endpoint"],
        method=raw.get("method", "POST"),
        headers=headers,
        # records_key: "" (empty) means the body is a bare JSON array of records
        records_key=raw.get("records_key", "records") or "",
        batch_size=int(raw.get("batch_size", 50)),
        max_retries=int(raw.get("max_retries", 3)),
        timeout=float(raw.get("timeout", 30.0)),
        fields=fields,
        response_map=rm,
        description=raw.get("description", ""),
    )


def profile_to_dict(p: Profile) -> dict:
    from dataclasses import asdict
    return asdict(p)


def profile_from_dict(d: dict) -> Profile:
    d = dict(d)
    d["fields"] = [FieldRule(**f) for f in d.get("fields", [])]
    d["response_map"] = ResponseMap(**d.get("response_map", {}))
    return Profile(**d)


# Column-name heuristics → sensible auto rules for ad-hoc (custom/playground) APIs.
_AUTO_RULES = [
    (re.compile(r"e[-_ ]?mail", re.I), "email"),
    (re.compile(r"phone|mobile|contact[-_ ]?no", re.I), "phone"),
    (re.compile(r"qty|quantity|count|units", re.I), "integer"),
    (re.compile(r"price|amount|value|rate|mrp|cost", re.I), "number"),
    (re.compile(r"date|_at$|_on$", re.I), "date"),
]


def infer_rules(headers: list[str]) -> list[FieldRule]:
    """Best-effort field rules from column names, so even a custom API entered
    in the UI gets meaningful validation with zero configuration."""
    rules = []
    for h in headers:
        ftype = "string"
        for rx, t in _AUTO_RULES:
            if rx.search(h):
                ftype = t
                break
        kwargs = {"name": h, "type": ftype}
        if ftype in ("integer", "number"):
            kwargs["min"] = 0
        rules.append(FieldRule(**kwargs))
    return rules


def build_custom_profile(endpoint: str, method: str, records_key: str,
                         batch_size: int, auth_token: str, headers: list[str],
                         results_path: str = "results", status_field: str = "status",
                         success_values: str = "success,ok,accepted",
                         message_field: str = "message",
                         index_field: str = "", auth_type: str = "token",
                         auth_user: str = "", auth_pass: str = "") -> Profile:
    """Builds a Profile from the web form — no YAML required."""
    import base64

    hdrs = {"Content-Type": "application/json"}
    if auth_type == "basic" and auth_user.strip():
        cred = base64.b64encode(
            f"{auth_user.strip()}:{auth_pass}".encode()).decode()
        hdrs["Authorization"] = f"Basic {cred}"
    elif auth_type == "token" and auth_token.strip():
        hdrs["Authorization"] = (auth_token if auth_token.lower().startswith(("bearer ", "basic "))
                                 else f"Bearer {auth_token.strip()}")
    return Profile(
        key="custom", name="Custom API (entered in UI)",
        endpoint=endpoint.strip(), method=method.upper(),
        headers=hdrs, records_key=records_key.strip() or "records",
        batch_size=max(1, min(int(batch_size or 50), 1000)),
        fields=infer_rules(headers),
        response_map=ResponseMap(
            results_path=results_path.strip() or "results",
            status_field=status_field.strip() or "status",
            success_values=[s.strip() for s in success_values.split(",") if s.strip()]
            or ["success", "ok", "accepted"],
            message_field=message_field.strip() or "message",
            index_field=index_field.strip() or None,
        ),
    )


def list_profiles(directory: Path | None = None) -> list[Profile]:
    directory = directory or PROFILES_DIR
    if not directory.exists():
        return []
    return [load_profile(p) for p in sorted(directory.glob("*.yaml"))]


def get_profile(key: str, directory: Path | None = None) -> Profile:
    directory = directory or PROFILES_DIR
    return load_profile(directory / f"{key}.yaml")
