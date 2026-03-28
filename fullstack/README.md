# MetroOps Shuttle & Depot Management Portal

Offline-first Flask + HTMX portal for reservations, service visibility, depot logistics, governance, and analytics.

## Quick Start

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Run with local TLS (self-signed):

```bash
python -m app.app
```

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
- TLS enforcement (set `DISABLE_TLS_ENFORCEMENT=1` for local tests only).
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

If `METROOPS_GATEWAY_TOKEN` is not configured, startup logs a warning and LAN gateway ingestion stays disabled.
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
4. Open `/notes`, confirm Cross-Task Rollups section loads from `/api/notes/rollup`.

## Optional Browser Integration Test

Offline banner browser test is in `API_tests/test_browser_offline_banner.py`.
It runs when Playwright is installed (`pip install playwright` and browser install step) and is skipped otherwise.

## Verification (Acceptance-Focused)

- Run all automated checks:

```bash
python -m pytest unit_tests API_tests
```

- Includes checks for:
  - cross-action nonce misuse rejection
  - kiosk abuse throttle + risk-event logging
  - experiment variant distribution near 50/50 at scale

## Maintainability Updates

- Database bootstrap and migration logic moved to `app/db_bootstrap.py` to reduce `app.py` coupling.
- Collaboration/knowledge routes (notes, social, experiments, metrics) moved to `app/routes_collab.py`.
- Operations routes (arrival board, booking, kiosk booking, depot inventory, ping ingest) moved to `app/routes_ops.py`.
- Runtime cache/artifact files are ignored via `fullstack/.gitignore`.

## Tests

Cross-platform (Windows/Linux/macOS):

```bash
python -m pytest unit_tests API_tests
```

Bash helper script (Linux/macOS or Git Bash):

```bash
./run_tests.sh
```
