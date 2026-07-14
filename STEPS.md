# STEPS.md — From this folder to a live, open-source web portal

Follow these in order. Total time: ~30 minutes.

## Phase 1 — Run it locally (5 min)

1. Install Python 3.11+ if needed: https://www.python.org/downloads/
2. In a terminal, from this folder:
   ```bash
   pip install -r requirements.txt
   uvicorn batchpilot.web.app:app --reload
   ```
3. Open http://127.0.0.1:8000, upload `sample_data/customers.xlsx`, keep **🧪 Playground** selected, click **Validate file**. Review the flagged rows, click **Send** — you'll see partial acceptance in action (row 2 rejected, rows 1 & 3 accepted) and can download the Excel report.
   To hit a real API instead, pick **🔗 Custom API** and type the endpoint URL, batch size and token directly in the form — no config files needed. Sending then requires ticking an explicit confirmation.
4. Optional — enable AI validation:
   ```bash
   cp .env.example .env
   # put your key in .env, then:
   export ANTHROPIC_API_KEY=sk-ant-...   # (or use a dotenv loader / your shell profile)
   ```
   Get a key at https://console.anthropic.com → API Keys.

## Phase 2 — Publish as open source on GitHub (10 min)

1. Create a GitHub account if needed, then a new **public** repository named `batchpilot` (no README/license — we have them). https://github.com/new
2. From this folder:
   ```bash
   git init
   git add .
   git commit -m "BatchPilot v0.1.0 — AI-guarded spreadsheet→API gateway"
   git branch -M main
   git remote add origin https://github.com/<your-username>/batchpilot.git
   git push -u origin main
   ```
3. Verify the **CI** tab turns green — GitHub Actions runs the tests and smoke-boots the app on every push (`.github/workflows/ci.yml`).
4. Make it look like a serious project (2 min each):
   - Repo → About → add description: *"AI-guarded gateway for pushing spreadsheet data into any REST API — validation, partial acceptance, per-row Excel reports."* + topics: `fastapi`, `data-quality`, `etl`, `ai`, `excel`.
   - Add a screenshot of the review page to the README.
   - Settings → enable Issues and Discussions.

   ⚠️ Never commit `.env`, real tokens, or company endpoints. Profiles use `${ENV_VAR}` substitution precisely so the repo stays clean. `.gitignore` already excludes `.env` and `data/`.

## Phase 3 — Deploy on Koyeb (free tier, normally no credit card)

Koyeb runs your Dockerfile straight from GitHub — no config files needed.

1. Push the repo to GitHub first (Phase 2).
2. Sign up at https://app.koyeb.com/auth/signup with your GitHub account
   (a card is only requested if they can't otherwise verify you're human).
3. **Create Web Service** → **GitHub** → select the `batchpilot` repo →
   builder: **Dockerfile** (auto-detected) → Instance: **Free** → port **8000**.
4. Under Environment variables, add:
   - `BATCHPILOT_USERNAME` = your username
   - `BATCHPILOT_PASSWORD` = your password (mark as secret)
   - `BATCHPILOT_SECRET`   = any long random string (mark as secret)
5. Deploy. In ~3 min you get `https://batchpilot-<org>.koyeb.app` — open it,
   sign in, run the Playground demo. Every `git push` to main redeploys automatically.

Free-tier notes: 512 MB RAM (plenty for BatchPilot), scales to zero after 1h
idle (cold start on next visit), storage ephemeral — job history resets on restarts.

## Phase 3-alt — Your own machine + Cloudflare Tunnel (free forever, always on)

Best option if any office PC/server can stay on: full control, persistent job
history, no sleeping, no card.

1. Install Docker Desktop, then from this folder: `docker compose up -d --build`
   → portal on `http://localhost:8000` and your office LAN.
2. Want a public HTTPS URL? Use a free Cloudflare Tunnel:
   ```bash
   # one-off quick tunnel (URL changes each run):
   cloudflared tunnel --url http://localhost:8000
   ```
   For a permanent URL, add your domain to Cloudflare (free plan) and create a
   named tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
3. Set login credentials in a `.env` file next to `docker-compose.yml`:
   `BATCHPILOT_USERNAME=...`, `BATCHPILOT_PASSWORD=...`, `BATCHPILOT_SECRET=...`

## Phase 4 — Point it at a real API (15 min, when ready)

1. Copy `profiles/example-real-api.yaml` → `profiles/<your-api>.yaml`.
2. Fill in endpoint, method, headers (secrets as `${VARS}`), `records_key`, `batch_size`.
3. Describe your columns under `fields` (required/type/regex/min/max/unique).
4. Map the response under `response_map` so partial acceptance is parsed per row.
5. Set the secret env vars (locally in `.env`; on Render in Environment settings), redeploy, and the profile appears in the dropdown.
6. Always do a **dry run** first: CLI `--dry-run`, or "Preview payload" in the UI before hitting Send.

## Phase 5 — Stand-out extras (optional)

- **Custom domain**: Render → Settings → Custom Domains (free HTTPS).
- **Demo video/GIF** in the README — 30s of upload→flags→send→report is worth 1000 words.
- **Auth**: the portal is open by default. Before exposing real endpoints, add basic auth (e.g. `fastapi` dependency checking a `BATCHPILOT_PASSWORD` env var) or deploy behind your company SSO/VPN.
- **Tag a release**: `git tag v0.1.0 && git push --tags`, then GitHub → Releases → draft release notes.
- **Share it**: post on LinkedIn / r/Python / Hacker News "Show HN" with the live demo link. The built-in mock API means anyone can try it in 30 seconds without credentials — that's your differentiator.

## Security checklist before real-world use

- [ ] No secrets in git history (`git log -p | grep -i token` to be sure)
- [ ] Auth added if the portal is public and profiles point at real APIs
- [ ] Rate limits of the target API respected via `batch_size` / `max_retries`
- [ ] `.env` only on the server, never committed
