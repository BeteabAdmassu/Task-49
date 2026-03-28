# Design Document

## Architecture
- **Runtime:** Single-node offline-first Flask application with SQLite as the local system of record.
- **UI pattern:** HTMX-driven dashboards for in-place updates every 10 seconds; no full page reload required.
- **Offline behavior:** Browser heartbeat every 10 seconds; failed heartbeats show `Offline—showing last known data` while leaving forms/actions available for local entry.
- **Data ingest:** Vehicle pings accepted only via local CSV upload endpoint (or LAN gateway integration endpoint using same table contract).
- **Security envelope:** RBAC permissions, lockout policy, 30-minute inactivity timeout, nonce checks on high-risk actions, risk event log.
- **Isolation model:** Learning records are depot-scoped for non-HR/non-admin staff.
- **Governance visibility:** HR/Admin reports page aggregates 7-day risk-event categories.

## Tech Stack
- Flask (web UI + REST-style endpoints)
- HTMX (partial updates)
- SQLite (reservations, logistics, notes, social graph, experiments, analytics)
- Cryptography/Fernet (sensitive field encryption at rest)
- Markdown (learning record rendering support)

## Database Schema
- **Auth & governance:** `users`, `permissions`, `sessions_nonce`, `risk_events`, `refresh_attempts`
- **Transport operations:** `routes`, `departures`, `schedules`, `vehicle_pings`
- **Reservation engine:** `seat_holds`, `bookings`, `rate_plans`
- **Depot logistics:** `warehouses`, `zones`, `bins`, `inventory_items`
- **Learning records:** `notes`, `note_versions`, `note_links`, `note_attachments`
- **Internal social:** `relationships`
- **Experiments & analytics:** `experiments`, `experiment_assignments`, `analytics_events`, `ranking_samples`

## Key Rule Implementations
- Seat hold timeout fixed at 8 minutes.
- Booking window enforced: minimum 2 hours before departure, maximum 30 days ahead.
- Hard inventory cap enforced via `BEGIN IMMEDIATE` transaction + seat availability check (never below zero).
- ETA fallback to scheduled mode when no ping for over 2 minutes.
- Risk events raised for impossible speed jumps (>85 mph delta behavior) and refresh spam (>30/min/user/screen).
- Speed anomaly checks include implied mph from geospatial distance/time between consecutive pings, with telemetry speed-delta retained as secondary signal.
- Strict server-side refresh cadence enforces one update every 10 seconds per actor/screen.
- Note rollback supports one-click restore from latest 20 historical versions.
- HTMX is served from local static assets to preserve full offline behavior.

## Maintainability Notes
- Database schema/seed/migration bootstrap is isolated in `repo/app/db_bootstrap.py`.
- Collaboration routes are isolated in `repo/app/routes_collab.py` to reduce domain coupling in the app entry file.
- Booking/depot/service-visibility routes are isolated in `repo/app/routes_ops.py`.
