"""Batched sender with retries + partial-acceptance response parsing."""

from __future__ import annotations

import time
from typing import Any

from .config import Profile


def _dig(obj: Any, dot_path: str):
    cur = obj
    for part in dot_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def parse_batch_response(profile: Profile, body: Any, batch: list[dict],
                         http_ok: bool) -> list[dict]:
    """Returns one outcome per record in the batch:
    {"status": "success"|"failed", "message": str}
    Handles per-record results matched by index OR by a record value (e.g.
    FieldAssist returns errors keyed by ERPId), plus whole-batch outcomes.
    """
    rm = profile.response_map
    batch_len = len(batch)
    results = _dig(body, rm.results_path) if isinstance(body, (dict, list)) else None
    if isinstance(body, list) and results is None:
        results = body  # API returns a bare list of per-record results

    if isinstance(results, list) and results:
        default = rm.missing_means if (http_ok and rm.missing_means != "unknown") \
            else "unknown"
        default_msg = "" if default == "success" else "no result returned"
        outcomes = [{"status": default, "message": default_msg}
                    for _ in range(batch_len)]

        # Optional value-based lookup: sent-record field ↔ result-item field.
        by_value: dict[str, list[int]] = {}
        if rm.match_field and rm.record_field:
            for i, rec in enumerate(batch):
                v = str(rec.get(rm.record_field, "")).strip()
                by_value.setdefault(v, []).append(i)

        for pos, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            idx = None
            if rm.match_field and rm.record_field and rm.match_field in item:
                candidates = by_value.get(str(item[rm.match_field]).strip())
                if candidates:
                    idx = candidates.pop(0)
            if idx is None:
                idx = pos
                if rm.index_field and rm.index_field in item:
                    try:
                        idx = int(item[rm.index_field])
                    except (TypeError, ValueError):
                        idx = pos
            if not 0 <= idx < batch_len:
                continue
            status_raw = str(item.get(rm.status_field, "")).lower()
            ok = status_raw in [str(s).lower() for s in rm.success_values]
            outcomes[idx] = {
                "status": "success" if ok else "failed",
                "message": str(item.get(rm.message_field, "") or ("" if ok else status_raw)),
            }
        return outcomes

    # No per-record detail — whole batch shares one outcome.
    if http_ok:
        return [{"status": "success", "message": ""}] * batch_len
    msg = str(body)[:300] if body is not None else "request failed"
    return [{"status": "failed", "message": msg}] * batch_len


def send_all(profile: Profile, rows: list[dict],
             progress_cb=None) -> list[dict]:
    """Sends rows in batches. Returns one outcome dict per row (aligned)."""
    import httpx

    outcomes: list[dict] = []
    with httpx.Client(timeout=profile.timeout) as client:
        for start in range(0, len(rows), profile.batch_size):
            batch = rows[start:start + profile.batch_size]
            # Empty records_key → API expects a bare JSON array (e.g. FieldAssist)
            body = {profile.records_key: batch} if profile.records_key else batch
            resp_body, http_ok = _send_with_retry(client, profile, body)
            outcomes.extend(parse_batch_response(profile, resp_body, batch, http_ok))
            if progress_cb:
                progress_cb(min(start + profile.batch_size, len(rows)), len(rows))
    return outcomes


def _send_with_retry(client, profile: Profile, body: dict):
    import httpx

    last_err = None
    for attempt in range(profile.max_retries):
        try:
            resp = client.request(profile.method, profile.endpoint,
                                  json=body, headers=profile.headers)
            try:
                parsed = resp.json()
            except ValueError:
                parsed = resp.text
            if resp.status_code in (429, 502, 503, 504) and attempt < profile.max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            # 2xx and even 207/400-with-details are handed to the parser;
            # partial acceptance often arrives with a 200 or 207.
            return parsed, 200 <= resp.status_code < 300 or resp.status_code == 207
        except httpx.HTTPError as e:
            last_err = e
            if attempt < profile.max_retries - 1:
                time.sleep(2 ** attempt)
    return f"network error: {last_err}", False
