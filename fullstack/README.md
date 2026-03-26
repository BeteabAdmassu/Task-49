# MetroOps Shuttle & Depot Management Portal

Offline-first Flask + HTMX portal for reservations, service visibility, depot logistics, governance, and analytics.

## Quick Start

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Run with local TLS (self-signed):

```bash
python app/app.py
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
- Nonce validation for booking confirmation and inventory adjustments.
- TLS enforcement (set `DISABLE_TLS_ENFORCEMENT=1` for local tests only).
- Risk events: impossible speed jumps and excessive refresh attempts.
- Notes are depot-scoped for non-HR/non-admin users.

## Maintainability Updates

- Database bootstrap and migration logic moved to `app/db_bootstrap.py` to reduce `app.py` coupling.
- Runtime cache/artifact files are ignored via `fullstack/.gitignore`.

## Tests

```bash
./run_tests.sh
```
