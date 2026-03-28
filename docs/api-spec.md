# API Specification

| Method | Path | Description | Params | Response |
|---|---|---|---|---|
| GET | `/api/heartbeat` | Per-screen heartbeat and refresh guard | `screen` | `{ ok, time }` |
| GET | `/api/arrival-board` | HTMX partial for next-stop ETA board | `route_id?` | HTML partial |
| GET | `/api/route-distribution` | HTMX partial for vehicles by route | - | HTML partial |
| GET | `/api/seat-availability/<departure_id>` | Seats remaining partial | `departure_id` | HTML partial |
| GET | `/api/seat-availability` | Seats remaining by query + screen key | `departure_id,screen?` | HTML partial |
| GET | `/api/departures/search` | Rider departure search | `route_code?` | JSON list |
| POST | `/api/security/nonce` | Mint nonce for high-risk action | `action` | `{ nonce }` |
| POST | `/api/bookings/hold` | Hold seats for 8 minutes | `departure_id,seats,product_type,bundle_days` | Hold metadata |
| POST | `/api/bookings/confirm` | Confirm booking with nonce | `hold_nonce,request_nonce,contact` | `{ ok,total_price }` |
| POST | `/api/kiosk/security/nonce` | Mint nonce for kiosk booking confirm | `action` | `{ nonce }` |
| POST | `/api/kiosk/bookings/hold` | Kiosk seat hold for 8 minutes | `departure_id,seats,product_type,bundle_days` | Hold metadata |
| POST | `/api/kiosk/bookings/confirm` | Kiosk booking confirm with nonce | `hold_nonce,request_nonce,contact` | `{ ok,total_price }` |
| POST | `/api/vehicle-pings/upload` | CSV ingestion for local vehicle pings | multipart file | `{ ok,inserted }` |
| POST | `/api/vehicle-pings/gateway` | LAN gateway vehicle ping ingestion | JSON `pings[]` + `X-Gateway-Token` | `{ ok,inserted,source }` |
| POST | `/api/depot/bins/<bin_id>/freeze` | Freeze/unfreeze bin | `frozen` | `{ ok,frozen }` |
| POST | `/api/depot/allocate` | Capacity-checked inventory allocation | `bin_id,volume_cuft,weight_lb,request_nonce` | `{ ok }` |
| GET | `/api/depot/hierarchy` | List warehouses/zones/bins | - | `{ warehouses,zones,bins }` |
| GET | `/api/depot/bin-rules` | List active/inactive bin rule values | - | JSON list |
| POST | `/api/depot/bin-rules` | Upsert bin type/status rule value | `rule_type,rule_value,is_active` | `{ ok }` |
| POST | `/api/depot/warehouses` | Create warehouse | `name` | `{ ok,id }` |
| POST | `/api/depot/zones` | Create zone under warehouse | `warehouse_id,name` | `{ ok,id }` |
| POST | `/api/depot/bins` | Create bin with metadata | `zone_id,code,bin_type,status,capacity_*` | `{ ok,id }` |
| POST | `/api/depot/bins/<bin_id>/metadata` | Update bin type/status | `bin_type?,status?` | `{ ok }` |
| POST | `/api/notes` | Create/update markdown record with versioning | note payload | `{ ok,id }` |
| GET | `/api/notes/<note_id>/versions` | Fetch latest 20 rollback versions | path param | JSON list |
| GET | `/api/notes/<note_id>/render` | Render note Markdown to HTML | path param | `{ note_id,title,html }` |
| POST | `/api/notes/<note_id>/attachments` | Upload note attachment (<=20MB) | multipart file | `{ ok }` |
| POST | `/api/notes/link` | Create bidirectional note links | `from_note_id,to_note_id,link_type` | `{ ok }` |
| POST | `/api/notes/<note_id>/rollback/<version_no>` | Rollback to historical version | path params | `{ ok }` |
| GET | `/api/notes/rollup` | Cross-task rollup summary | - | JSON list |
| POST | `/api/social/action` | Follow/block/report/favorite/like operations | `target_user_id,relation` | `{ ok }` |
| GET | `/api/experiments/assign/<widget_key>` | Deterministic 50/50 A/B assignment | `widget_key` | `{ variant,label }` |
| GET | `/reports` | HR/Admin governance report dashboard | - | HTML page |
| POST | `/admin/users` | Admin local user provisioning with password policy | `username,password,role,depot_assignment` | `{ ok }` |

Notes:
- `GET /api/heartbeat` now returns HTTP `429` after more than 30 refresh attempts/minute/user/screen.
- `GET /api/heartbeat` also enforces a strict minimum interval of 10 seconds per actor/screen.
- Session-authenticated mutating routes require CSRF token via `X-CSRF-Token` header or form field.
- LAN gateway ingestion is disabled unless `METROOPS_GATEWAY_TOKEN` is configured.
