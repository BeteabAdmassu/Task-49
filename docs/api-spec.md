# API Specification

| Method | Path | Description | Params | Response |
|---|---|---|---|---|
| GET | `/api/heartbeat` | Per-screen heartbeat and refresh guard | `screen` | `{ ok, time }` |
| GET | `/api/arrival-board` | HTMX partial for next-stop ETA board | `route_id?` | HTML partial |
| GET | `/api/route-distribution` | HTMX partial for vehicles by route | - | HTML partial |
| GET | `/api/seat-availability/<departure_id>` | Seats remaining partial | `departure_id` | HTML partial |
| GET | `/api/departures/search` | Rider departure search | `route_code?` | JSON list |
| POST | `/api/security/nonce` | Mint nonce for high-risk action | `action` | `{ nonce }` |
| POST | `/api/bookings/hold` | Hold seats for 8 minutes | `departure_id,seats,product_type,bundle_days` | Hold metadata |
| POST | `/api/bookings/confirm` | Confirm booking with nonce | `hold_nonce,request_nonce,contact` | `{ ok,total_price }` |
| POST | `/api/kiosk/security/nonce` | Mint nonce for kiosk booking confirm | `action` | `{ nonce }` |
| POST | `/api/kiosk/bookings/hold` | Kiosk seat hold for 8 minutes | `departure_id,seats,product_type,bundle_days` | Hold metadata |
| POST | `/api/kiosk/bookings/confirm` | Kiosk booking confirm with nonce | `hold_nonce,request_nonce,contact` | `{ ok,total_price }` |
| POST | `/api/vehicle-pings/upload` | CSV ingestion for local vehicle pings | multipart file | `{ ok,inserted }` |
| POST | `/api/depot/bins/<bin_id>/freeze` | Freeze/unfreeze bin | `frozen` | `{ ok,frozen }` |
| POST | `/api/depot/allocate` | Capacity-checked inventory allocation | `bin_id,volume_cuft,weight_lb,request_nonce` | `{ ok }` |
| POST | `/api/notes` | Create/update markdown record with versioning | note payload | `{ ok,id }` |
| POST | `/api/notes/<note_id>/attachments` | Upload note attachment (<=20MB) | multipart file | `{ ok }` |
| POST | `/api/notes/link` | Create bidirectional note links | `from_note_id,to_note_id,link_type` | `{ ok }` |
| POST | `/api/notes/<note_id>/rollback/<version_no>` | Rollback to historical version | path params | `{ ok }` |
| GET | `/api/notes/rollup` | Cross-task rollup summary | - | JSON list |
| POST | `/api/social/action` | Follow/block/report/favorite/like operations | `target_user_id,relation` | `{ ok }` |
| GET | `/api/experiments/assign/<widget_key>` | Deterministic 50/50 A/B assignment | `widget_key` | `{ variant,label }` |

Notes:
- `GET /api/heartbeat` now returns HTTP `429` after more than 30 refresh attempts/minute/user/screen.
