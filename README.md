# ⇪ BatchPilot

**The AI-guarded gateway between your spreadsheets and any REST API.**

Everyone has that workflow: someone hands you an Excel sheet, and its rows need to end up in an API — a CRM, an ERP, an internal bulk endpoint. Doing it by hand is slow; naive scripts push garbage data and give you no per-row visibility, especially when the API **partially accepts** a batch.

BatchPilot fixes the whole pipeline:

```
 spreadsheet ──► rule validation ──► AI semantic validation ──► review UI
                                                                  │
 per-row Excel report ◄── partial-acceptance parsing ◄── batched send
```

## Features

- **Any API, zero code** — non-coders enter the API URL, batch size and token straight in the web form (column rules are auto-detected from headers). Power users can save reusable YAML *profiles* with full field rules. Secrets stay in env vars, so profiles are safe to commit.
- **Playground mode** — want to just play? One click sends to a built-in fake API that simulates partial acceptance. Nothing real is ever touched, no credentials needed.
- **Two-layer validation**
  - *Rules engine* (offline, deterministic): required fields, types (email/phone/date/number), regex, ranges, uniqueness/duplicates.
  - *AI semantic validation* (optional, via Claude): catches what regexes can't — placeholder values (`9999999999`, `test`), data in the wrong column, outliers, typos in categoricals. Degrades gracefully when no API key is set.
- **Human-in-the-loop** — nothing is sent until you review flagged rows; optionally hold back rows with errors and send the rest. Custom-API sends additionally require an explicit "I understand this sends real data" confirmation.
- **Guard rails** — user-entered endpoints are checked (http/https only, no private/internal hosts) before any request leaves the server.
- **Partial acceptance done right** — when the API accepts row 1 and 3 but rejects row 2, BatchPilot maps each per-record result back to your original spreadsheet rows.
- **Per-row Excel report** — original data + validation flags + real API outcome, color-coded, downloadable.
- **Web portal + CLI** — same engine behind both. Job history in SQLite.
- **Login protected** — username/password sign-in page; credentials set via `BATCHPILOT_USERNAME` / `BATCHPILOT_PASSWORD` env vars (defaults: `admin` / `batchpilot` — change before going public).
- **Built-in mock API** — `/mock/ingest` simulates a partial-acceptance endpoint so anyone can demo BatchPilot with zero setup.

## Quick start (local)

```bash
git clone https://github.com/<you>/batchpilot && cd batchpilot
pip install -r requirements.txt
uvicorn batchpilot.web.app:app --reload
# open http://127.0.0.1:8000 → upload sample_data/customers.xlsx → profile "Demo"
```

Or with Docker:

```bash
docker compose up --build   # http://localhost:8000
```

Optional AI validation:

```bash
cp .env.example .env        # add ANTHROPIC_API_KEY
```

## CLI

```bash
python -m batchpilot.cli data.xlsx --profile demo --dry-run
python -m batchpilot.cli data.xlsx --profile demo --ai --send --skip-errors --report results.xlsx
```

Exit code is non-zero if any row was rejected — CI/cron friendly.

## Adding your own API

1. Copy `profiles/example-real-api.yaml` → `profiles/my-api.yaml`.
2. Set `endpoint`, `headers` (use `${MY_TOKEN}` for secrets), `records_key`, `batch_size`.
3. Declare `fields` rules matching your spreadsheet headers.
4. Map the response: where the per-record results list lives (`results_path`), which field is the status (`status_field`), which values mean success, and (optionally) which field carries the record index.

That's it — the profile appears in the web UI dropdown automatically.

## Deploying

See **[STEPS.md](STEPS.md)** for the complete step-by-step guide: publishing to GitHub, one-click deploy on Render's free tier, Docker self-hosting, and hardening tips.

## Tech

Python 3.11+ · FastAPI · openpyxl · httpx · SQLite · Anthropic API (optional) · no JS build step.

## License

MIT — see [LICENSE](LICENSE).
