# MetroOps Shuttle & Depot Management Portal

Offline-first Flask + HTMX portal for reservations, service visibility, depot logistics, governance, and analytics.

## Docker Quick Start

From the project root (the folder that contains `docker-compose.yml`), start everything with:

1. Set a strong gateway token in your shell (required by compose, do not use placeholder values):

```bash
set METROOPS_GATEWAY_TOKEN=use-a-long-random-token
```

2. Start all services:

```bash
docker compose up
```

This single command builds and starts the full stack with no manual dependency setup.

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

2. Run with local TLS (self-signed):

```bash
python -m app.app
```

If you need to run local HTTP (non-TLS) for development-only testing, explicitly set development runtime mode before disabling TLS enforcement:

```bash
set METROOPS_RUNTIME_ENV=development
set DISABLE_TLS_ENFORCEMENT=1
python -m app.app
```

In production-like runtime modes (`production`, default when unset), `DISABLE_TLS_ENFORCEMENT=1` is rejected at startup.

Enable debug mode only when needed:

```bash
set FLASK_DEBUG=1
```

3. Open `https://localhost:5000`.

## Seed Accounts

- `agent01` / `MetroOpsPass!01`
- `supervisor01` / `MetroOpsPass!02`
- `hr01` / `MetroOpsPass!03`
- `admin01` / `MetroOpsPass!04`

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

```bash
set METROOPS_GATEWAY_TOKEN=your-strong-local-token
```

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

## Non-Docker Verification for New Depot Controls

1. Start the app locally with `python -m app.app`.
2. Login as `supervisor01` and open `https://localhost:5000/depot/manage`.
3. Set Bin ID `1` to frozen in **Freeze or Unfreeze Bin**, then try **Allocate Inventory** with small values (expect an error because the bin is frozen).
4. Set Bin ID `1` to unfrozen and retry allocation (expect success).
5. Click **Refresh Hierarchy** and confirm `current_cuft`/`current_lb` increased for that bin.

## Optional Browser Integration Test

Offline banner browser test is in `API_tests/test_browser_offline_banner.py`.
It runs when Playwright is installed and is skipped otherwise.

Playwright setup (optional):

```bash
python -m pip install playwright
python -m playwright install chromium
python -m pytest API_tests/test_browser_offline_banner.py -q
```

Non-browser offline signaling coverage is also included in `API_tests/test_ui_and_risk.py::test_offline_banner_contract_server_and_client_paths`, so CI/local verification does not rely only on Playwright.

## Verification (Acceptance-Focused)

- Run all automated checks:

```bash
python -m pytest unit_tests API_tests
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
- Runtime cache/artifact files are ignored via `.gitignore` at the repository root.

## Tests

Cross-platform (Windows/Linux/macOS):

```bash
python -m pytest unit_tests API_tests
```

Bash helper script (Linux/macOS or Git Bash):

```bash
./run_tests.sh
```
