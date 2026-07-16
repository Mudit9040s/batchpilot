"""BatchPilot web portal (FastAPI)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import ipaddress
import json
import socket
from urllib.parse import urlparse

from .. import __version__
from ..ai_validate import ai_available, validate_with_ai
from ..config import (build_custom_profile, get_profile, list_profiles,
                      profile_from_dict, profile_to_dict)
from ..ingest import is_payload_file, read_payload_files, read_rows
from ..report import write_report
from ..rules import validate_rows
from ..sender import send_all, send_payload_files
from .. import store

app = FastAPI(title="BatchPilot", version=__version__)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

REPORTS_DIR = Path(os.environ.get("BATCHPILOT_DATA_DIR", "data")) / "reports"

# ---------------------------------------------------------------------------
# Authentication — username/password login page, session cookie.
# Credentials come from env vars (BATCHPILOT_USERNAME / BATCHPILOT_PASSWORD).
# ---------------------------------------------------------------------------
import secrets as _secrets

from starlette.middleware.sessions import SessionMiddleware

AUTH_USER = os.environ.get("BATCHPILOT_USERNAME", "admin")
AUTH_PASS = os.environ.get("BATCHPILOT_PASSWORD", "batchpilot")
USING_DEFAULT_CREDS = "BATCHPILOT_PASSWORD" not in os.environ

# Paths reachable without login. /mock/ingest stays open: it is the fake demo
# API and is called server-to-server (no cookies) by the sender.
_PUBLIC_PATHS = ("/login", "/health", "/mock/ingest")

# Public landing page: the project-story presentation. Shown to logged-out
# visitors at "/"; its "Live Demo" button links to /login.
_PRESENTATION_PATH = Path(__file__).resolve().parents[2] / "docs" / "presentation.html"


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path not in _PUBLIC_PATHS and not request.session.get("user"):
        if path == "/" and _PRESENTATION_PATH.exists():
            return FileResponse(_PRESENTATION_PATH, media_type="text/html")
        # Only send users back to pages that can be GET-loaded after login;
        # POST-only paths (like /upload) would 405.
        nxt = path if request.method == "GET" else "/"
        return RedirectResponse(f"/login?next={nxt}", status_code=303)
    return await call_next(request)


# IMPORTANT: added AFTER require_login. In Starlette the middleware added
# last runs first, so SessionMiddleware must be registered last to have
# request.session ready before the login check reads it.
app.add_middleware(SessionMiddleware,
                   secret_key=os.environ.get("BATCHPILOT_SECRET")
                   or _secrets.token_hex(32),
                   max_age=60 * 60 * 12)  # 12h sessions


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {
        "next": next, "default_creds": USING_DEFAULT_CREDS})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...),
                 password: str = Form(...), next: str = Form("/")):
    ok = (_secrets.compare_digest(username, AUTH_USER)
          and _secrets.compare_digest(password, AUTH_PASS))
    if not ok:
        return templates.TemplateResponse(request, "login.html", {
            "next": next, "default_creds": USING_DEFAULT_CREDS,
            "error": "Wrong username or password."}, status_code=401)
    request.session["user"] = username
    # Never land on POST-only endpoints; default to the app home.
    safe_next = next if (next.startswith("/") and not next.startswith("//")
                         and next not in ("/upload", "/login")) else "/"
    return RedirectResponse(safe_next, status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _merge_issues(a: list[list[dict]], b: list[list[dict]] | None) -> list[list[dict]]:
    if not b:
        return a
    return [x + y for x, y in zip(a, b)]


def _url_error(url: str) -> str | None:
    """Basic safety check for user-entered endpoints (blocks obvious SSRF)."""
    try:
        u = urlparse(url)
    except ValueError:
        return "That doesn't look like a valid URL."
    if u.scheme not in ("http", "https") or not u.hostname:
        return "Endpoint must start with http:// or https://"
    host = u.hostname
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return "Endpoints on this server itself aren't allowed — use Playground mode instead."
    try:
        addr = socket.getaddrinfo(host, None)[0][4][0]
        if ipaddress.ip_address(addr).is_private:
            return "Private/internal network endpoints aren't allowed from the portal."
    except (socket.gaierror, ValueError, IndexError):
        return f"Could not resolve host '{host}' — check the URL."
    return None


def _index_ctx(request, **extra):
    return {"profiles": list_profiles(), "ai_on": ai_available(),
            "version": __version__, **extra}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _index_ctx(request))


@app.post("/upload")
def upload(request: Request,
                 file: UploadFile = File(...),
                 mode: str = Form("playground"),
                 profile_key: str = Form(""),
                 api_url: str = Form(""),
                 method: str = Form("POST"),
                 records_key: str = Form(""),
                 batch_size: int = Form(100),
                 delay: float = Form(1.0),
                 stringify: str | None = Form(None),
                 auth_type: str = Form("basic"),
                 auth_token: str = Form(""),
                 auth_user: str = Form(""),
                 auth_pass: str = Form(""),
                 field_map: str = Form(""),
                 results_path: str = Form("results"),
                 status_field: str = Form("status"),
                 success_values: str = Form("success,ok,accepted"),
                 message_field: str = Form("message"),
                 index_field: str = Form(""),
                 use_ai: str | None = Form(None)):
    # Sync handler on purpose: FastAPI runs it in the threadpool, so slow
    # validation of one user's big file never blocks other users.
    data = file.file.read()

    # JSON-files workflow: a .json (object/array) or a .zip of .json payloads.
    # Each payload is sent as ONE request (like the Colab GRN/invoice scripts).
    raw_payloads = None
    if is_payload_file(file.filename or ""):
        try:
            items = read_payload_files(data, file.filename or "upload.json")
        except Exception:
            items = []
        if not items:
            return templates.TemplateResponse(
                request, "index.html",
                _index_ctx(request, error="No JSON payloads found in the file."),
                status_code=400)
        headers = ["File", "Top-level keys"]
        rows = []
        issues = []
        raw_payloads = []
        for it in items:
            keys = (", ".join(list(it["payload"].keys())[:8])
                    if isinstance(it["payload"], dict) else "(not an object)")
            rows.append({"File": it["name"], "Top-level keys": keys})
            issues.append([{"field": "File", "code": "json",
                            "message": it["error"], "severity": "error"}]
                          if it["error"] else [])
            raw_payloads.append(it["payload"])
        if mode == "custom":
            err = _url_error(api_url)
            if err:
                return templates.TemplateResponse(request, "index.html",
                                                  _index_ctx(request, error=err),
                                                  status_code=400)
            profile = build_custom_profile(api_url, method, records_key, batch_size,
                                           auth_token, [], results_path,
                                           status_field, success_values,
                                           message_field, index_field,
                                           auth_type=auth_type, auth_user=auth_user,
                                           auth_pass=auth_pass)
        elif mode == "profile" and profile_key:
            profile = get_profile(profile_key)
        else:
            mode = "playground"
            profile = get_profile("demo")
        job_id = store.create_job(profile.key, file.filename or "upload", headers,
                                  rows, issues, False,
                                  profile_dict=profile_to_dict(profile), mode=mode,
                                  raw_payloads=raw_payloads)
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    try:
        headers, rows = read_rows(data, file.filename or "upload.xlsx")
    except ValueError as e:
        return templates.TemplateResponse(request, "index.html",
                                          _index_ctx(request, error=str(e)),
                                          status_code=400)
    if not rows:
        return templates.TemplateResponse(
            request, "index.html",
            _index_ctx(request, error="No data rows found in the file."),
            status_code=400)

    # Apply the user's column→API-field mapping (Custom API advanced table).
    # Renames keys; a blank target means "don't send this column at all".
    if mode == "custom" and field_map.strip():
        try:
            fmap = {str(k): str(v).strip() for k, v in json.loads(field_map).items()}
        except (ValueError, AttributeError):
            fmap = {}
        if fmap:
            headers = [fmap.get(h, h) for h in headers if fmap.get(h, h) != ""]
            rows = [{fmap.get(k, k): v for k, v in r.items() if fmap.get(k, k) != ""}
                    for r in rows]

    if mode == "custom":
        err = _url_error(api_url)
        if err:
            return templates.TemplateResponse(request, "index.html",
                                              _index_ctx(request, error=err),
                                              status_code=400)
        if stringify:
            # Base-script semantics (safe()): every value becomes a stripped
            # string; blanks/nan become null.
            rows = [{k: (None if v is None or str(v).strip() in ("", "nan", "None")
                         else str(v).strip()) for k, v in r.items()} for r in rows]
        profile = build_custom_profile(api_url, method, records_key, batch_size,
                                       auth_token, headers, results_path,
                                       status_field, success_values,
                                       message_field, index_field,
                                       auth_type=auth_type, auth_user=auth_user,
                                       auth_pass=auth_pass, delay=delay)
    elif mode == "profile" and profile_key:
        profile = get_profile(profile_key)
    else:
        mode = "playground"
        profile = get_profile("demo")

    issues = validate_rows(profile, rows)
    ai_used = False
    if use_ai and ai_available():
        ai_issues = validate_with_ai(rows, profile.field_names or headers)
        if ai_issues is not None:
            issues = _merge_issues(issues, ai_issues)
            ai_used = True

    job_id = store.create_job(profile.key, file.filename or "upload", headers,
                              rows, issues, ai_used,
                              profile_dict=profile_to_dict(profile), mode=mode)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


def _job_profile(job: dict):
    p = job["payload"].get("profile")
    return profile_from_dict(p) if p else get_profile(job["profile_key"])


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_view(request: Request, job_id: str):
    job = store.get_job(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    p = job["payload"]
    profile = _job_profile(job)
    raw = p.get("raw")
    rows_view = []
    for i, row in enumerate(p["rows"]):
        outcome = p["outcomes"][i] if p.get("outcomes") else None
        rows_view.append({"n": i + 1, "data": row, "issues": p["issues"][i],
                          "outcome": outcome,
                          "row_json": json.dumps(raw[i] if raw else row, indent=2,
                                                 ensure_ascii=False, default=str),
                          "has_error": any(x["severity"] == "error" for x in p["issues"][i])})
    # Exact request body the API will receive (first records of the first batch)
    if raw:
        sample_payload = raw[0]
        sample_note = "one request per JSON file — this is the body of request 1"
    else:
        first = p["rows"][:min(profile.batch_size, 3)]
        sample_payload = {profile.records_key: first} if profile.records_key else first
        sample_note = (f"bare JSON array, {profile.batch_size} records per request"
                       if not profile.records_key else
                       f"wrapped in '{profile.records_key}', {profile.batch_size} records per request")
    return templates.TemplateResponse(request, "job.html", {
        "job": job, "profile": profile, "headers": p["headers"],
        "rows": rows_view, "sent": job["status"] == "sent",
        "sending": job["status"] == "sending",
        "mode": p.get("mode", "profile"),
        "sample_payload": sample_payload, "sample_note": sample_note,
    })


@app.post("/job/{job_id}/send")
def job_send(job_id: str, skip_errors: str | None = Form(None),
             confirm: str | None = Form(None)):
    job = store.get_job(job_id)
    if not job:
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    p = job["payload"]
    profile = _job_profile(job)
    if p.get("mode") == "custom" and not confirm:
        # Safety net: real-API sends require the explicit confirmation checkbox.
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    if not store.claim_send(job_id):
        # Already sent or being sent (double-click / two tabs / two users).
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    rows, issues = p["rows"], p["issues"]

    send_idx = [i for i in range(len(rows))
                if not (skip_errors and any(x["severity"] == "error" for x in issues[i]))]
    outcomes_all = [{"status": "skipped", "message": "held back (validation errors)"}
                    for _ in rows]
    if send_idx:
        raw = p.get("raw")
        if raw:  # JSON-files workflow: one request per payload
            sent_outcomes = send_payload_files(profile, [raw[i] for i in send_idx])
        else:
            sent_outcomes = send_all(profile, [rows[i] for i in send_idx])
        for pos, i in enumerate(send_idx):
            outcomes_all[i] = sent_outcomes[pos]

    report_path = REPORTS_DIR / f"{job_id}.xlsx"
    write_report(report_path, p["headers"], rows, issues, outcomes_all)
    store.mark_sent(job_id, outcomes_all, str(report_path))
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.get("/job/{job_id}/report.xlsx")
def job_report(job_id: str):
    job = store.get_job(job_id)
    if not job or not job.get("report_path") or not Path(job["report_path"]).exists():
        return HTMLResponse("Report not available", status_code=404)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(job["filename"]).stem)
    return FileResponse(job["report_path"],
                        filename=f"batchpilot_{safe_name}_{job_id}.xlsx")


_GUIDE_PATH = Path(__file__).resolve().parents[2] / "docs" / "index.html"


@app.get("/guide", response_class=HTMLResponse)
def guide():
    """In-app user guide (login-protected, same file as the public docs/)."""
    if _GUIDE_PATH.exists():
        return FileResponse(_GUIDE_PATH, media_type="text/html")
    return HTMLResponse("Guide not found — docs/index.html missing from the deployment.",
                        status_code=404)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return templates.TemplateResponse(request, "history.html",
                                      {"jobs": store.list_jobs()})


# ---------------------------------------------------------------------------
# Built-in mock API — lets anyone demo BatchPilot (incl. partial acceptance)
# without a real target API. Point the 'demo' profile at /mock/ingest.
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


@app.post("/mock/ingest")
async def mock_ingest(request: Request):
    body = await request.json()
    records = body.get("records", [])
    results = []
    for i, rec in enumerate(records):
        problems = []
        for k, v in rec.items():
            sv = str(v) if v is not None else ""
            if "email" in k.lower() and sv and not EMAIL_RE.match(sv):
                problems.append(f"invalid email '{sv}'")
            if "phone" in k.lower() and sv and not re.fullmatch(r"\+?\d{10,13}", re.sub(r"[ \-()]", "", sv)):
                problems.append(f"invalid phone '{sv}'")
            if ("qty" in k.lower() or "quantity" in k.lower() or "price" in k.lower()):
                try:
                    if sv != "" and float(str(sv).replace(",", "")) < 0:
                        problems.append(f"{k} cannot be negative")
                except ValueError:
                    problems.append(f"{k} must be numeric")
        missing = [k for k, v in rec.items() if v is None or str(v).strip() == ""]
        if missing:
            problems.append(f"missing: {', '.join(missing)}")
        results.append({
            "index": i,
            "status": "accepted" if not problems else "rejected",
            "message": "; ".join(problems),
        })
    accepted = sum(1 for r in results if r["status"] == "accepted")
    return JSONResponse({"summary": {"received": len(records), "accepted": accepted,
                                     "rejected": len(records) - accepted},
                         "results": results})


@app.get("/health")
def health():
    return {"ok": True, "version": __version__, "ai": ai_available()}
