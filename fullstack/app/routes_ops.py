import csv
import math
import secrets
from datetime import timedelta

from flask import jsonify, render_template, request, session


def register_ops_routes(app, ctx):
    login_required = ctx["login_required"]
    require_permission = ctx["require_permission"]
    get_db = ctx["get_db"]
    utc_now = ctx["utc_now"]
    to_iso = ctx["to_iso"]
    from_iso = ctx["from_iso"]
    format_clock = ctx["format_clock"]
    log_risk = ctx["log_risk"]
    available_seats = ctx["available_seats"]
    assert_nonce = ctx["assert_nonce"]
    create_booking_hold_for_user = ctx["create_booking_hold_for_user"]
    confirm_booking_for_user = ctx["confirm_booking_for_user"]
    ensure_kiosk_user_id = ctx["ensure_kiosk_user_id"]
    gateway_token = app.config.get("GATEWAY_TOKEN", "")
    allowed_bin_types = {"standard", "cold", "hazmat", "secure"}
    allowed_bin_statuses = {"available", "unavailable", "maintenance"}

    def great_circle_miles(lat1, lon1, lat2, lon2):
        r = 3958.7613
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c

    def json_payload_or_400():
        payload = request.get_json(silent=True)
        if payload is None:
            return None, (jsonify({"error": "Invalid JSON payload"}), 400)
        return payload, None

    def parse_ping_row(row):
        try:
            vehicle_id = row.get("vehicle_id")
            route_raw = row.get("route_id")
            if not vehicle_id or route_raw is None:
                return None
            return {
                "vehicle_id": vehicle_id,
                "route_id": int(route_raw),
                "stop_sequence": int(row.get("stop_sequence", 1) or 1),
                "speed_mph": float(row.get("speed_mph", 0) or 0),
                "ping_time": row.get("ping_time") or to_iso(utc_now()),
                "lat": float(row.get("lat", 0) or 0),
                "lon": float(row.get("lon", 0) or 0),
            }
        except (TypeError, ValueError):
            return None

    def insert_ping(db, parsed, source, user_id=None):
        previous = db.execute(
            "SELECT speed_mph,ping_time,lat,lon FROM vehicle_pings WHERE vehicle_id=? ORDER BY ping_time DESC LIMIT 1",
            (parsed["vehicle_id"],),
        ).fetchone()
        if previous:
            delta_hours = max(
                1 / 3600,
                (from_iso(parsed["ping_time"]) - from_iso(previous["ping_time"])).total_seconds() / 3600,
            )
            implied_speed = great_circle_miles(previous["lat"], previous["lon"], parsed["lat"], parsed["lon"]) / delta_hours
            if implied_speed > 85:
                log_risk(
                    "impossible_speed_jump",
                    f"vehicle={parsed['vehicle_id']}, implied_mph={round(implied_speed,2)}, source={source}",
                    user_id,
                )
            if abs(parsed["speed_mph"] - previous["speed_mph"]) / delta_hours > 85:
                log_risk(
                    "impossible_speed_jump",
                    f"vehicle={parsed['vehicle_id']}, speed_delta_from={previous['speed_mph']}, speed_delta_to={parsed['speed_mph']}, source={source}",
                    user_id,
                )
        db.execute(
            "INSERT INTO vehicle_pings (vehicle_id,route_id,stop_sequence,lat,lon,speed_mph,ping_time,source) VALUES (?,?,?,?,?,?,?,?)",
            (
                parsed["vehicle_id"],
                parsed["route_id"],
                parsed["stop_sequence"],
                parsed["lat"],
                parsed["lon"],
                parsed["speed_mph"],
                parsed["ping_time"],
                source,
            ),
        )

    @app.get("/api/heartbeat")
    def heartbeat():
        return jsonify({"ok": True, "time": format_clock(utc_now())})

    @app.get("/api/arrival-board")
    def arrival_board():
        db = get_db()
        now = utc_now()
        route_id = request.args.get("route_id", type=int)
        rows = []
        route_filter = "WHERE r.id=?" if route_id else ""
        params = (route_id,) if route_id else ()
        routes = db.execute(f"SELECT r.id, r.code FROM routes r {route_filter} ORDER BY r.code", params).fetchall()

        for route in routes:
            latest_ping = db.execute(
                "SELECT * FROM vehicle_pings WHERE route_id=? ORDER BY ping_time DESC LIMIT 1",
                (route["id"],),
            ).fetchone()
            live = latest_ping and (now - from_iso(latest_ping["ping_time"]) <= timedelta(minutes=2))
            next_stop = db.execute(
                "SELECT stop_name, scheduled_arrival, stop_sequence FROM schedules WHERE route_id=? ORDER BY stop_sequence LIMIT 1",
                (route["id"],),
            ).fetchone()
            if not next_stop:
                continue
            if live:
                eta = from_iso(next_stop["scheduled_arrival"]) + timedelta(minutes=2)
                mode = "Live"
            else:
                eta = from_iso(next_stop["scheduled_arrival"])
                mode = "Scheduled"
            rows.append(
                {
                    "route_code": route["code"],
                    "stop_name": next_stop["stop_name"],
                    "eta_display": format_clock(eta),
                    "mode": mode,
                }
            )

        return render_template("partials/arrival_board.html", rows=rows, last_updated=format_clock(now))

    @app.get("/api/route-distribution")
    @login_required
    def route_distribution():
        db = get_db()
        data = db.execute(
            """
            SELECT r.code, COUNT(DISTINCT vp.vehicle_id) AS active_vehicles
            FROM routes r
            LEFT JOIN vehicle_pings vp ON vp.route_id=r.id
              AND vp.ping_time >= ?
            GROUP BY r.id
            ORDER BY r.code
            """,
            (to_iso(utc_now() - timedelta(minutes=2)),),
        ).fetchall()
        return render_template(
            "partials/route_distribution.html",
            rows=data,
            last_updated=format_clock(utc_now()),
        )

    @app.get("/api/seat-availability/<int:departure_id>")
    def seat_availability_partial(departure_id):
        seats = available_seats(get_db(), departure_id)
        return render_template(
            "partials/seat_availability.html",
            seats=seats,
            last_updated=format_clock(utc_now()),
        )

    @app.get("/api/seat-availability")
    def seat_availability_query():
        departure_id = request.args.get("departure_id", type=int)
        if not departure_id:
            return jsonify({"error": "departure_id is required"}), 422
        seats = available_seats(get_db(), departure_id)
        return render_template(
            "partials/seat_availability.html",
            seats=seats,
            last_updated=format_clock(utc_now()),
        )

    @app.post("/api/security/nonce")
    @login_required
    def create_nonce():
        action = request.form.get("action", "")
        if not action:
            return jsonify({"error": "Action required"}), 400
        nonce = secrets.token_urlsafe(24)
        get_db().execute(
            "INSERT INTO sessions_nonce (user_id,action,nonce,expires_at) VALUES (?,?,?,?)",
            (session["user_id"], action, nonce, to_iso(utc_now() + timedelta(minutes=10))),
        )
        get_db().commit()
        return jsonify({"nonce": nonce})

    @app.post("/api/bookings/hold")
    @login_required
    @require_permission("booking:create")
    def create_hold():
        payload, error = json_payload_or_400()
        if error:
            return error
        response, error, status = create_booking_hold_for_user(session["user_id"], payload)
        if error:
            return jsonify(error), status
        return jsonify(response), status

    @app.post("/api/bookings/confirm")
    @login_required
    @require_permission("booking:create")
    def confirm_booking():
        payload, error = json_payload_or_400()
        if error:
            return error
        hold_nonce = payload.get("hold_nonce")
        request_nonce = payload.get("request_nonce")
        contact = payload.get("contact", "")
        response, error, status = confirm_booking_for_user(session["user_id"], hold_nonce, request_nonce, contact)
        if error:
            return jsonify(error), status
        return jsonify(response), status

    @app.post("/api/kiosk/security/nonce")
    def create_kiosk_nonce():
        action = request.form.get("action", "")
        if not action:
            return jsonify({"error": "Action required"}), 400
        db = get_db()
        kiosk_user_id = ensure_kiosk_user_id(db)
        nonce = secrets.token_urlsafe(24)
        db.execute(
            "INSERT INTO sessions_nonce (user_id,action,nonce,expires_at) VALUES (?,?,?,?)",
            (kiosk_user_id, action, nonce, to_iso(utc_now() + timedelta(minutes=10))),
        )
        db.commit()
        return jsonify({"nonce": nonce})

    @app.post("/api/kiosk/bookings/hold")
    def kiosk_create_hold():
        payload, error = json_payload_or_400()
        if error:
            return error
        kiosk_user_id = ensure_kiosk_user_id(get_db())
        response, error, status = create_booking_hold_for_user(kiosk_user_id, payload)
        if error:
            return jsonify(error), status
        return jsonify(response), status

    @app.post("/api/kiosk/bookings/confirm")
    def kiosk_confirm_booking():
        payload, error = json_payload_or_400()
        if error:
            return error
        hold_nonce = payload.get("hold_nonce")
        request_nonce = payload.get("request_nonce")
        contact = payload.get("contact", "")
        kiosk_user_id = ensure_kiosk_user_id(get_db())
        response, error, status = confirm_booking_for_user(kiosk_user_id, hold_nonce, request_nonce, contact)
        if error:
            return jsonify(error), status
        return jsonify(response), status

    @app.post("/api/vehicle-pings/upload")
    @login_required
    def upload_vehicle_pings():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "CSV file required"}), 400
        text = file.read().decode("utf-8")
        reader = csv.DictReader(text.splitlines())
        db = get_db()
        inserted = 0
        invalid_rows = 0
        for row in reader:
            parsed = parse_ping_row(row)
            if not parsed:
                invalid_rows += 1
                continue
            insert_ping(db, parsed, "csv", session.get("user_id"))
            inserted += 1
        db.commit()
        if invalid_rows:
            app.logger.warning("vehicle_ping_upload_invalid_rows source=csv invalid_rows=%s", invalid_rows)
        return jsonify({"ok": True, "inserted": inserted})

    @app.post("/api/vehicle-pings/gateway")
    def gateway_vehicle_pings():
        if not gateway_token:
            return jsonify({"error": "Gateway ingestion disabled: token not configured"}), 503
        provided = request.headers.get("X-Gateway-Token", "")
        if provided != gateway_token:
            return jsonify({"error": "Invalid gateway token"}), 403
        payload, error = json_payload_or_400()
        if error:
            return error
        pings = payload.get("pings", [])
        if not isinstance(pings, list):
            return jsonify({"error": "pings must be a list"}), 422

        db = get_db()
        inserted = 0
        invalid_rows = 0
        for row in pings:
            if not isinstance(row, dict):
                invalid_rows += 1
                continue
            parsed = parse_ping_row(row)
            if not parsed:
                invalid_rows += 1
                continue
            insert_ping(db, parsed, "lan_gateway", None)
            inserted += 1
        db.commit()
        if invalid_rows:
            app.logger.warning("vehicle_ping_upload_invalid_rows source=lan_gateway invalid_rows=%s", invalid_rows)
        return jsonify({"ok": True, "inserted": inserted, "source": "lan_gateway"})

    @app.post("/api/depot/bins/<int:bin_id>/freeze")
    @login_required
    @require_permission("depot:manage")
    def freeze_bin(bin_id):
        try:
            frozen = int(request.form.get("frozen", "1"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid frozen value"}), 422
        get_db().execute("UPDATE bins SET frozen=? WHERE id=?", (frozen, bin_id))
        get_db().commit()
        return jsonify({"ok": True, "frozen": bool(frozen)})

    @app.post("/api/depot/allocate")
    @login_required
    @require_permission("depot:manage")
    def allocate_inventory():
        payload, error = json_payload_or_400()
        if error:
            return error
        request_nonce = payload.get("request_nonce")
        ok, msg = assert_nonce(session["user_id"], "inventory_adjust", request_nonce)
        if not ok:
            return jsonify({"error": msg}), 409
        try:
            bin_id = int(payload["bin_id"])
            vol = float(payload["volume_cuft"])
            weight = float(payload["weight_lb"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Invalid bin_id/volume_cuft/weight_lb"}), 422

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT * FROM bins WHERE id=?", (bin_id,)).fetchone()
            if not row:
                db.rollback()
                return jsonify({"error": "Bin not found"}), 404
            if row["frozen"]:
                db.rollback()
                return jsonify({"error": "Bin is frozen"}), 409
            if row["current_cuft"] + vol > row["capacity_cuft"] or row["current_lb"] + weight > row["capacity_lb"]:
                db.rollback()
                return jsonify({"error": "Bin capacity exceeded"}), 422

            db.execute(
                "INSERT INTO inventory_items (bin_id,item_name,volume_cuft,weight_lb,created_at) VALUES (?,?,?,?,?)",
                (bin_id, payload.get("item_name", "Asset"), vol, weight, to_iso(utc_now())),
            )
            db.execute(
                "UPDATE bins SET current_cuft=current_cuft+?, current_lb=current_lb+? WHERE id=?",
                (vol, weight, bin_id),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify({"ok": True})

    @app.get("/depot/manage")
    @login_required
    @require_permission("depot:manage")
    def depot_manage_page():
        return render_template("depot_manage.html")

    @app.get("/api/depot/hierarchy")
    @login_required
    @require_permission("depot:manage")
    def depot_hierarchy():
        db = get_db()
        warehouses = [dict(row) for row in db.execute("SELECT id,name FROM warehouses ORDER BY name").fetchall()]
        zones = [dict(row) for row in db.execute("SELECT id,warehouse_id,name FROM zones ORDER BY name").fetchall()]
        bins = [
            dict(row)
            for row in db.execute(
                "SELECT id,zone_id,code,bin_type,status,frozen,capacity_cuft,capacity_lb,current_cuft,current_lb FROM bins ORDER BY code"
            ).fetchall()
        ]
        return jsonify({"warehouses": warehouses, "zones": zones, "bins": bins})

    @app.post("/api/depot/warehouses")
    @login_required
    @require_permission("depot:manage")
    def create_warehouse():
        payload, error = json_payload_or_400()
        if error:
            return error
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Warehouse name is required"}), 422
        db = get_db()
        exists = db.execute("SELECT 1 FROM warehouses WHERE name=?", (name,)).fetchone()
        if exists:
            return jsonify({"error": "Warehouse already exists"}), 409
        cursor = db.execute("INSERT INTO warehouses (name) VALUES (?)", (name,))
        db.commit()
        return jsonify({"ok": True, "id": cursor.lastrowid}), 201

    @app.post("/api/depot/zones")
    @login_required
    @require_permission("depot:manage")
    def create_zone():
        payload, error = json_payload_or_400()
        if error:
            return error
        name = (payload.get("name") or "").strip()
        try:
            warehouse_id = int(payload.get("warehouse_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "Valid warehouse_id is required"}), 422
        if not name:
            return jsonify({"error": "Zone name is required"}), 422

        db = get_db()
        warehouse = db.execute("SELECT id FROM warehouses WHERE id=?", (warehouse_id,)).fetchone()
        if not warehouse:
            return jsonify({"error": "Warehouse not found"}), 404
        exists = db.execute("SELECT 1 FROM zones WHERE warehouse_id=? AND name=?", (warehouse_id, name)).fetchone()
        if exists:
            return jsonify({"error": "Zone already exists in warehouse"}), 409
        cursor = db.execute("INSERT INTO zones (warehouse_id,name) VALUES (?,?)", (warehouse_id, name))
        db.commit()
        return jsonify({"ok": True, "id": cursor.lastrowid}), 201

    @app.post("/api/depot/bins")
    @login_required
    @require_permission("depot:manage")
    def create_bin():
        payload, error = json_payload_or_400()
        if error:
            return error
        code = (payload.get("code") or "").strip()
        bin_type = (payload.get("bin_type") or "").strip()
        status = (payload.get("status") or "").strip()
        try:
            zone_id = int(payload.get("zone_id"))
            capacity_cuft = float(payload.get("capacity_cuft"))
            capacity_lb = float(payload.get("capacity_lb"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid zone_id/capacity values"}), 422

        if not code:
            return jsonify({"error": "Bin code is required"}), 422
        if bin_type not in allowed_bin_types:
            return jsonify({"error": f"Invalid bin_type. Allowed: {sorted(allowed_bin_types)}"}), 422
        if status not in allowed_bin_statuses:
            return jsonify({"error": f"Invalid status. Allowed: {sorted(allowed_bin_statuses)}"}), 422
        if capacity_cuft <= 0 or capacity_lb <= 0:
            return jsonify({"error": "Capacities must be > 0"}), 422

        db = get_db()
        zone = db.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone()
        if not zone:
            return jsonify({"error": "Zone not found"}), 404
        duplicate = db.execute("SELECT 1 FROM bins WHERE zone_id=? AND code=?", (zone_id, code)).fetchone()
        if duplicate:
            return jsonify({"error": "Bin code already exists in zone"}), 409
        cursor = db.execute(
            "INSERT INTO bins (zone_id,code,bin_type,capacity_cuft,capacity_lb,status) VALUES (?,?,?,?,?,?)",
            (zone_id, code, bin_type, capacity_cuft, capacity_lb, status),
        )
        db.commit()
        return jsonify({"ok": True, "id": cursor.lastrowid}), 201

    @app.post("/api/depot/bins/<int:bin_id>/metadata")
    @login_required
    @require_permission("depot:manage")
    def update_bin_metadata(bin_id):
        payload, error = json_payload_or_400()
        if error:
            return error
        bin_type = payload.get("bin_type")
        status = payload.get("status")
        updates = []
        values = []
        if bin_type is not None:
            if bin_type not in allowed_bin_types:
                return jsonify({"error": f"Invalid bin_type. Allowed: {sorted(allowed_bin_types)}"}), 422
            updates.append("bin_type=?")
            values.append(bin_type)
        if status is not None:
            if status not in allowed_bin_statuses:
                return jsonify({"error": f"Invalid status. Allowed: {sorted(allowed_bin_statuses)}"}), 422
            updates.append("status=?")
            values.append(status)
        if not updates:
            return jsonify({"error": "No metadata fields provided"}), 422

        db = get_db()
        exists = db.execute("SELECT id FROM bins WHERE id=?", (bin_id,)).fetchone()
        if not exists:
            return jsonify({"error": "Bin not found"}), 404
        values.append(bin_id)
        db.execute(f"UPDATE bins SET {', '.join(updates)} WHERE id=?", tuple(values))
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/departures/search")
    def search_departures():
        route_code = request.args.get("route_code")
        db = get_db()
        rows = db.execute(
            """
            SELECT d.id, r.code, r.origin, r.destination, d.departure_time, d.base_price
            FROM departures d JOIN routes r ON r.id=d.route_id
            WHERE (? IS NULL OR r.code = ?)
            ORDER BY d.departure_time
            LIMIT 25
            """,
            (route_code, route_code),
        ).fetchall()
        response = []
        for row in rows:
            response.append(
                {
                    "departure_id": row["id"],
                    "route": row["code"],
                    "origin": row["origin"],
                    "destination": row["destination"],
                    "departure_time": row["departure_time"],
                    "seats_remaining": available_seats(db, row["id"]),
                    "base_price": row["base_price"],
                }
            )
        return jsonify(response)
