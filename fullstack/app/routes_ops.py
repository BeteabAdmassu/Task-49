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
        user_id = session.get("user_id", 0)
        screen = request.args.get("screen", "unknown")
        now = utc_now()
        bucket = now.strftime("%Y-%m-%dT%H:%M")
        previous_bucket = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
        db = get_db()
        actor_key = f"user:{user_id}" if user_id else f"anon:{session.setdefault('anon_refresh_id', secrets.token_hex(12))}"

        cadence = db.execute(
            "SELECT last_seen FROM refresh_cadence WHERE actor_key=? AND screen=?",
            (actor_key, screen),
        ).fetchone()
        if cadence and (now - from_iso(cadence["last_seen"])) < timedelta(seconds=10):
            retry_after = max(1, 10 - int((now - from_iso(cadence["last_seen"])) .total_seconds()))
            return jsonify({"error": "Refresh allowed once every 10 seconds", "retry_after_seconds": retry_after}), 429

        db.execute(
            "INSERT INTO refresh_cadence (actor_key,screen,last_seen) VALUES (?,?,?) ON CONFLICT(actor_key,screen) DO UPDATE SET last_seen=excluded.last_seen",
            (actor_key, screen, to_iso(now)),
        )
        db.commit()

        if user_id:
            row = db.execute(
                "SELECT attempt_count FROM refresh_attempts WHERE user_id=? AND screen=? AND minute_bucket=?",
                (user_id, screen, bucket),
            ).fetchone()
            count = (row["attempt_count"] if row else 0) + 1
            if row:
                db.execute(
                    "UPDATE refresh_attempts SET attempt_count=? WHERE user_id=? AND screen=? AND minute_bucket=?",
                    (count, user_id, screen, bucket),
                )
            else:
                db.execute(
                    "INSERT INTO refresh_attempts (user_id,screen,minute_bucket,attempt_count) VALUES (?,?,?,?)",
                    (user_id, screen, bucket, count),
                )
            db.commit()

            rolling = db.execute(
                "SELECT COALESCE(SUM(attempt_count),0) FROM refresh_attempts WHERE user_id=? AND screen=? AND minute_bucket IN (?,?)",
                (user_id, screen, bucket, previous_bucket),
            ).fetchone()[0]
            if rolling > 30:
                log_risk("excessive_refresh", f"screen={screen}, count_60s={rolling}", user_id)
                return jsonify({"error": "Refresh rate exceeded", "retry_after_seconds": 10}), 429

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
