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

## Maintainability Updates

- Database bootstrap and migration logic moved to `app/db_bootstrap.py` to reduce `app.py` coupling.
- Collaboration/knowledge routes (notes, social, experiments, metrics) moved to `app/routes_collab.py`.
- Operations routes (arrival board, booking, kiosk booking, depot inventory, ping ingest) moved to `app/routes_ops.py`.
- Runtime cache/artifact files are ignored via `fullstack/.gitignore`.

## Tests

```bash
./run_tests.sh
```
