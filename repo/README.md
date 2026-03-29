# MetroOps Shuttle & Depot Management Portal

Offline-first Flask + HTMX portal for reservations, service visibility, depot logistics, governance, and analytics.

## Docker Quick Start

From the project root (the folder that contains `docker-compose.yml`), start everything with:

1. Optional (recommended for consistency): create `.env` from `.env.example` and adjust values.

Windows CMD: `copy .env.example .env`

PowerShell: `Copy-Item .env.example .env`

macOS/Linux: `cp .env.example .env`

2. Start all services:

```bash
docker compose up
```

This single command builds and starts the full stack with no manual dependency setup. In development runtime, a local gateway token is auto-generated if not provided.

## Services and Ports

- `metroops` (Flask app): `https://localhost:5000` (mapped from container port `5000`)

## Verify It Is Working

1. Start with `docker compose up` and wait until the `metroops` container is healthy.
2. Open `https://localhost:5000/login` in your browser (self-signed cert warning is expected in local Docker).
3. Sign in with `supervisor01` / `MetroOpsPass!02` and confirm `/dashboard` loads.
4. Optional CLI check:

```bash
curl -k https://localhost:5000/login
```

You should receive an HTML response for the login page.

## Local (Non-Docker) Quick Start

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Choose one runtime mode and use the matching startup command.

Development/local mode (recommended for first run)

- Required: `METROOPS_RUNTIME_ENV=development`
- Optional: `FLASK_SECRET` (auto-generated if omitted in development mode)

PowerShell:

```powershell
$env:METROOPS_RUNTIME_ENV='development'
python -m app.app
```

bash (macOS/Linux/Git Bash):

```bash
export METROOPS_RUNTIME_ENV=development
python -m app.app
```

Development local HTTP mode (no TLS; only for local testing)

PowerShell:

```powershell
$env:METROOPS_RUNTIME_ENV='development'
$env:DISABLE_TLS_ENFORCEMENT='1'
$env:SESSION_COOKIE_SECURE='0'
python -m app.app
```

bash (macOS/Linux/Git Bash):

```bash
export METROOPS_RUNTIME_ENV=development
export DISABLE_TLS_ENFORCEMENT=1
export SESSION_COOKIE_SECURE=0
python -m app.app
```

Production-like local mode (security checks enforced)

- Required: `METROOPS_RUNTIME_ENV=production`
- Required: `FLASK_SECRET`
- Required on first run for DB bootstrap: `METROOPS_BOOTSTRAP_ADMIN_PASSWORD` (>=12 chars)
- Optional: `METROOPS_BOOTSTRAP_ADMIN_USERNAME` (defaults to `admin`)

PowerShell:

```powershell
$env:METROOPS_RUNTIME_ENV='production'
$env:FLASK_SECRET='replace-with-strong-secret'
$env:METROOPS_BOOTSTRAP_ADMIN_PASSWORD='replace-with-strong-admin-password'
python -m app.app
```

bash (macOS/Linux/Git Bash):

```bash
export METROOPS_RUNTIME_ENV=production
export FLASK_SECRET='replace-with-strong-secret'
export METROOPS_BOOTSTRAP_ADMIN_PASSWORD='replace-with-strong-admin-password'
python -m app.app
```

Notes:

- In development/test/local runtime with `DISABLE_TLS_ENFORCEMENT=1`, the app forces non-secure session cookies for local HTTP continuity and logs a warning.
- In production-like runtime (`production`, default when unset), `DISABLE_TLS_ENFORCEMENT=1` is rejected at startup.

3. Open `https://localhost:5000/login` (or `http://localhost:5000/login` when running local HTTP mode).

### Local TLS Certificates (Optional Explicit Certs)

Flask can run with adhoc TLS by default, but you can also generate explicit local cert files.

PowerShell (requires OpenSSL in PATH):

```powershell
openssl req -x509 -newkey rsa:2048 -keyout local-key.pem -out local-cert.pem -days 365 -nodes -subj "/CN=localhost"
python -c "from app.app import app; app.run(host='0.0.0.0', port=5000, ssl_context=('local-cert.pem','local-key.pem'))"
```

bash:

```bash
openssl req -x509 -newkey rsa:2048 -keyout local-key.pem -out local-cert.pem -days 365 -nodes -subj "/CN=localhost"
python -c "from app.app import app; app.run(host='0.0.0.0', port=5000, ssl_context=('local-cert.pem','local-key.pem'))"
```

## Frontend Verification Quick Path

1. Start app in development mode:

PowerShell:

```powershell
$env:METROOPS_RUNTIME_ENV='development'
python -m app.app
```

bash:

```bash
export METROOPS_RUNTIME_ENV=development
python -m app.app
```

2. Open `https://localhost:5000/login` and sign in (dev seed account example: `supervisor01` / `MetroOpsPass!02`).
3. Sanity-check these pages:
   - `/dashboard`
   - `/kiosk`
   - `/depot/manage`
   - `/notes`

## Social Features UI

- Where to find controls:
  - Dashboard quick action panel: `/dashboard` (Social Actions panel).
  - Profile-level controls with state: `/profiles/<user_id>` (Follow/Unfollow, Favorite, Like, Block, Report).
- Quick manual verification:
  1. Login as `agent01` and open `/profiles/2`.
  2. Click **Follow** and confirm status feedback updates.
  3. Login as `supervisor01`, follow `agent01`, then switch back to `agent01` and refresh `/profiles/2`.
  4. Confirm **Mutual follow active** appears and follow control shows **Unfollow**.
  5. Try **Block** or **Report** and confirm success/error feedback appears in the status area.
- Test command:
  - `./run_tests.sh`
  - PowerShell: `./run_tests.ps1`

## Kiosk Session Attribution

- Purpose:
  - Kiosk bookings remain anonymous (shared `kiosk_rider` account) while adding per-browser-session traceability.
- Stored attribution fields:
  - `seat_holds.kiosk_session_id`
  - `bookings.kiosk_session_id`
  - `analytics_events.metadata` for `booking_confirmed` includes `kiosk_session_id`
- Behavior:
  - Kiosk hold/confirm accepts optional `kiosk_session_id`.
  - If omitted, server generates a safe fallback session ID and returns it in API responses.
  - The same session ID propagates hold -> confirm -> booking analytics metadata.
- Quick verification:
  1. Open `/kiosk` and create a hold.
  2. Confirm booking.
  3. Verify responses include `kiosk_session_id`.
  4. Run tests to verify DB propagation and fallback behavior.

## Seed Accounts

Development/test runtime seeds the following local accounts:

- `agent01` / `MetroOpsPass!01`
- `supervisor01` / `MetroOpsPass!02`
- `hr01` / `MetroOpsPass!03`
- `admin01` / `MetroOpsPass!04`

Production-like runtime does not seed default accounts. First run requires:

- `FLASK_SECRET`
- `METROOPS_BOOTSTRAP_ADMIN_PASSWORD` (min 12 chars)
- optional `METROOPS_BOOTSTRAP_ADMIN_USERNAME` (defaults to `admin`)

The app fails startup if development default credentials are detected in non-development runtime.

## Security Controls Implemented

- 12-character minimum password policy enforced by login/create constraints.
- 5 failed logins triggers 15-minute lockout.
- Session inactivity timeout: 30 minutes.
- CSRF protection enforced for session-authenticated mutating routes.
- Nonce validation for booking confirmation and inventory adjustments.
- Nonce enforcement is user-bound, action-bound, one-time-use, and expiry-checked.
- Kiosk abuse throttling on unauthenticated endpoints with 429 + retry hints and risk-event logging.
- Vehicle ping CSV ingest is permission-gated with `ops:ingest` (supervisor/admin by default).
- TLS enforcement is mandatory in production-like mode; disabling is allowed only in explicit `development`/`test`/`local` runtime modes with warning logs.
- Risk events: impossible speed jumps and excessive refresh attempts.
- Notes are depot-scoped for non-HR/non-admin users.

## Operations Ingestion

- CSV upload endpoint: `/api/vehicle-pings/upload`
- LAN gateway endpoint: `/api/vehicle-pings/gateway` (requires `X-Gateway-Token`)
- Refresh heartbeat enforces strict max frequency: once every 10 seconds per actor/screen.
- Speed anomaly detection uses implied travel speed between consecutive pings (lat/lon + time), with telemetry speed-delta as a secondary heuristic.

Set a non-default gateway token in your environment before enabling LAN ingest:

Windows CMD: `set METROOPS_GATEWAY_TOKEN=your-strong-local-token`

PowerShell: `$env:METROOPS_GATEWAY_TOKEN='your-strong-local-token'`

macOS/Linux: `export METROOPS_GATEWAY_TOKEN='your-strong-local-token'`

If `METROOPS_GATEWAY_TOKEN` is missing or placeholder-like (for example `replace-me-for-production`), startup logs a warning and LAN gateway ingestion stays disabled.
- HTMX is vendored locally in `app/static/vendor/htmx.min.js` for offline operation.

## New Operational Surfaces

- Live seat availability polling: `/api/seat-availability?departure_id=<id>&screen=dashboard-seat-availability`
- Depot hierarchy management page: `/depot/manage`
- Depot hierarchy APIs:
  - `GET /api/depot/hierarchy`
  - `GET /api/depot/bin-rules`
  - `POST /api/depot/bin-rules`
  - `POST /api/depot/warehouses`
  - `POST /api/depot/zones`
  - `POST /api/depot/bins`
  - `POST /api/depot/bins/<id>/metadata`
  - `GET /api/config/booking-rules`
  - `POST /api/config/booking-rules`

Booking rules are DB-backed and audit-logged (`config_audit_log`) with safe defaults:
- min advance booking: 2h
- max booking horizon: 30d
- commuter bundle minimum: 3d
- seat hold timeout: 8m

## Verification Steps

1. Login as `supervisor01` and open `/dashboard`.
2. Confirm seat availability auto-refreshes every 10s after selecting departure.
3. Open `/depot/manage`, create warehouse/zone/bin, then update bin type/status.
4. In `/depot/manage`, use **Freeze or Unfreeze Bin** for bin `1`, then run **Allocate Inventory** for the same bin and confirm status messages appear and tables refresh.
5. Confirm allocation fails when bin is frozen and succeeds after unfreezing.
6. Open `/notes`, confirm Cross-Task Rollups section loads from `/api/notes/rollup`.
7. Open `/kiosk` and confirm the hold button label reflects current policy (for example `Hold Seat (8 min)` or updated configured value).

## Non-Docker Verification for New Depot Controls

1. Start the app locally with `python -m app.app`.
2. Login as `supervisor01` and open `https://localhost:5000/depot/manage`.
3. Set Bin ID `1` to frozen in **Freeze or Unfreeze Bin**, then try **Allocate Inventory** with small values (expect an error because the bin is frozen).
4. Set Bin ID `1` to unfrozen and retry allocation (expect success).
5. Click **Refresh Hierarchy** and confirm `current_cuft`/`current_lb` increased for that bin.

## Browser Integration Tests

Offline banner browser test is in `API_tests/test_browser_offline_banner.py`.
It is required in CI (`CI=1`) and skipped locally when Playwright is not installed.

Playwright setup (optional):

```bash
python -m pip install playwright
python -m playwright install chromium
python -m pytest API_tests/test_browser_offline_banner.py -q
```

Non-browser offline signaling coverage is also included in `API_tests/test_ui_and_risk.py::test_offline_banner_contract_server_and_client_paths`, so CI/local verification does not rely only on Playwright.

## Verification (Acceptance-Focused)

- Run all automated checks (canonical cross-platform):

```bash
./run_tests.sh
```

PowerShell canonical command:

```powershell
./run_tests.ps1
```

PowerShell helper (recommended on Windows):

```powershell
./run_tests.ps1
```

If your environment has restrictive temp/cache permissions, pre-create runtime dirs and run:

PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path .pytest_runtime,.pytest_runtime\tmp,.pytest_runtime\cache | Out-Null
./run_tests.ps1
```

bash:

```bash
mkdir -p .pytest_runtime/tmp .pytest_runtime/cache
./run_tests.sh
```

- Includes checks for:
  - cross-action nonce misuse rejection
  - kiosk abuse throttle + risk-event logging
  - experiment variant distribution near 50/50 at scale

## Local Data Cleanup (Non-Production)

Use this only for local verification resets.

1. Remove local DB and key (they are recreated on next run):

```bash
del data\metroops.db
del data\secret.key
```

2. Clear uploaded attachments but keep the placeholder file:

```bash
for %f in (data\attachments\*) do @if /I not "%~nxf"==".keep" del "%f"
```

3. Restart app to re-seed baseline data:

```bash
python -m app.app
```

## Maintainability Updates

- Database bootstrap and migration logic moved to `app/db_bootstrap.py` to reduce `app.py` coupling.
- Collaboration/knowledge routes (notes, social, experiments, metrics) moved to `app/routes_collab.py`.
- Operations routes (arrival board, booking, kiosk booking, depot inventory, ping ingest) moved to `app/routes_ops.py`.
- Core web/auth/config/reporting routes are modularized in `app/core_routes.py`.
- Security request guards (TLS, CSRF, session timeout, refresh throttling) are modularized in `app/security_middleware.py`.
- Runtime policy and env validation helpers are centralized in `app/config.py`.
- Runtime cache/artifact files are ignored via `.gitignore` at the repository root.

## Tests

Cross-platform (Windows/Linux/macOS):

```bash
./run_tests.sh
```

Windows PowerShell:

```powershell
./run_tests.ps1
```

Linux/macOS/Git Bash:

```bash
./run_tests.sh
```

If pytest temp/cache directory permissions are restrictive:

```bash
./run_tests.sh
```

## Troubleshooting Startup Errors

- `RuntimeError: FLASK_SECRET must be explicitly set in non-development runtime`
  - Cause: running in production-like mode without `FLASK_SECRET`.
  - Fix: set `METROOPS_RUNTIME_ENV=development` for local quick start, or set `FLASK_SECRET` for production-like mode.

- `Non-development bootstrap requires METROOPS_BOOTSTRAP_ADMIN_PASSWORD ...`
  - Cause: first run in production-like mode without bootstrap admin password.
  - Fix: set `METROOPS_BOOTSTRAP_ADMIN_PASSWORD` (>=12 chars), optionally `METROOPS_BOOTSTRAP_ADMIN_USERNAME`.

- `Development default credentials detected in non-development runtime...`
  - Cause: DB was initialized with dev seed users, then runtime switched to production-like.
  - Fix: reinitialize with production-like bootstrap env vars and a fresh DB.

## Troubleshooting Test Runner (WinError 5)

- `PermissionError: [WinError 5] Access is denied` during pytest temp/cache cleanup.
  - Cause: restrictive filesystem policy or locked temp directories.
  - Fix:
    1. Use repo-local runtime dirs and helper script: `./run_tests.ps1`.
    2. If needed, pre-create writable dirs and use explicit basetemp:
       - `New-Item -ItemType Directory -Force -Path .pytest_runtime,.pytest_runtime\tmp,.pytest_runtime\cache | Out-Null`
       - `./run_tests.ps1`
  - Recovery: close IDE/file-indexer handles on `.pytest_runtime` and rerun.

Bash helper script (Linux/macOS or Git Bash):

```bash
./run_tests.sh
```
