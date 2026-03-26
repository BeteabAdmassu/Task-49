import csv
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from cryptography.fernet import Fernet
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from .db_bootstrap import initialize_database
except ImportError:
    from db_bootstrap import initialize_database

try:
    import markdown
except Exception:  # pragma: no cover
    markdown = None


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
DB_PATH = Path(os.environ.get("METROOPS_DB_PATH", str(DATA_DIR / "metroops.db")))
KEY_PATH = Path(os.environ.get("METROOPS_KEY_PATH", str(DATA_DIR / "secret.key")))

ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
MIN_PASSWORD_LENGTH = 12


def utc_now():
    return datetime.now(UTC)


def to_iso(dt):
    return dt.astimezone(UTC).isoformat()


def from_iso(value):
    return datetime.fromisoformat(value)


def format_clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def load_fernet():
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
    return Fernet(KEY_PATH.read_bytes())


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", secrets.token_hex(32))
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
    app.config["TLS_REQUIRED"] = True
    app.config["DISABLE_TLS_ENFORCEMENT"] = os.environ.get("DISABLE_TLS_ENFORCEMENT", "0") == "1"
    app.fernet = load_fernet()

    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_exc=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        initialize_database(DB_PATH, utc_now, to_iso)

    def current_user():
        user_id = session.get("user_id")
        if not user_id:
            return None
        return get_db().execute(
            "SELECT id,username,role,depot_assignment FROM users WHERE id=?", (user_id,)
        ).fetchone()

    def user_permissions(role):
        rows = get_db().execute("SELECT permission FROM permissions WHERE role=?", (role,)).fetchall()
        return {r["permission"] for r in rows}

    def login_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            return fn(*args, **kwargs)

        return wrapper

    def require_permission(permission):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                user = current_user()
                if not user:
                    abort(401)
                perms = user_permissions(user["role"])
                if permission not in perms and "admin:all" not in perms:
                    abort(403)
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    def mask_face_identifier(value):
        if not value:
            return ""
        return f"{value[:2]}***{value[-2:]}"

    def password_policy_error(password):
        if len(password or "") < MIN_PASSWORD_LENGTH:
            return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        return None

    def is_note_manager(user):
        return user and user["role"] in {"hr", "admin"}

    def can_view_note(user, note_row):
        if not user:
            return False
        if is_note_manager(user):
            return True
        return note_row["depot_scope"] == user["depot_assignment"]

    def can_edit_note(user, note_row):
        return bool(user and can_view_note(user, note_row) and (is_note_manager(user) or note_row["owner_id"] == user["id"]))

    def ensure_kiosk_user_id(db):
        row = db.execute("SELECT id FROM users WHERE username='kiosk_rider'").fetchone()
        if row:
            return row["id"]
        kiosk_password = "KioskModeOnly!99"
        policy_error = password_policy_error(kiosk_password)
        if policy_error:
            raise ValueError(policy_error)
        now = to_iso(utc_now())
        cursor = db.execute(
            "INSERT INTO users (username,password_hash,role,depot_assignment,created_at) VALUES (?,?,?,?,?)",
            ("kiosk_rider", generate_password_hash(kiosk_password), "employee", "Kiosk", now),
        )
        db.commit()
        return cursor.lastrowid

    def log_risk(event_type, details, user_id=None):
        get_db().execute(
            "INSERT INTO risk_events (user_id,event_type,details,created_at) VALUES (?,?,?,?)",
            (user_id, event_type, details[:600], to_iso(utc_now())),
        )
        get_db().commit()

    def html_from_md(text):
        if markdown is None:
            return text.replace("\n", "<br>")
        return markdown.markdown(text)

    def cleanup_expired_holds(db):
        now_iso = to_iso(utc_now())
        db.execute(
            "UPDATE seat_holds SET status='expired' WHERE status='active' AND expires_at <= ?",
            (now_iso,),
        )

    def available_seats(db, departure_id):
        cleanup_expired_holds(db)
        departure = db.execute(
            "SELECT total_seats FROM departures WHERE id=?", (departure_id,)
        ).fetchone()
        if not departure:
            return 0
        held = db.execute(
            "SELECT COALESCE(SUM(seats),0) FROM seat_holds WHERE departure_id=? AND status='active'",
            (departure_id,),
        ).fetchone()[0]
        booked = db.execute(
            "SELECT COALESCE(SUM(seats),0) FROM bookings WHERE departure_id=? AND status='confirmed'",
            (departure_id,),
        ).fetchone()[0]
        return max(0, departure["total_seats"] - held - booked)

    def enforce_booking_window(departure_time):
        now = utc_now()
        if departure_time < now + timedelta(hours=2):
            return False, "Booking must be made at least 2 hours before departure"
        if departure_time > now + timedelta(days=30):
            return False, "Booking cannot be made more than 30 days in advance"
        return True, "ok"

    def price_for_departure(db, departure, seats):
        base = departure["base_price"]
        dep_date = from_iso(departure["departure_time"]).date().isoformat()
        plan = db.execute(
            "SELECT amount_delta FROM rate_plans WHERE start_date <= ? AND end_date >= ? ORDER BY id DESC LIMIT 1",
            (dep_date, dep_date),
        ).fetchone()
        delta = plan["amount_delta"] if plan else 0
        return round((base + delta) * seats, 2)

    def create_booking_hold_for_user(user_id, payload):
        departure_id = int(payload.get("departure_id"))
        seats_requested = int(payload.get("seats", 1))
        bundle_days = int(payload.get("bundle_days", 1))
        product_type = payload.get("product_type", "single")

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            departure = db.execute("SELECT * FROM departures WHERE id=?", (departure_id,)).fetchone()
            if not departure:
                db.rollback()
                return None, {"error": "Invalid departure"}, 404

            valid, message = enforce_booking_window(from_iso(departure["departure_time"]))
            if not valid:
                db.rollback()
                return None, {"error": message}, 422

            if product_type == "commuter_bundle" and bundle_days < 3:
                db.rollback()
                return None, {"error": "Commuter bundle requires minimum 3 days"}, 422

            seats = available_seats(db, departure_id)
            if seats_requested <= 0 or seats_requested > seats:
                db.rollback()
                return None, {"error": "Insufficient seats", "seats_remaining": seats}, 409

            hold_nonce = secrets.token_urlsafe(16)
            expires_at = to_iso(utc_now() + timedelta(minutes=8))
            db.execute(
                "INSERT INTO seat_holds (departure_id,user_id,seats,expires_at,status,nonce,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    departure_id,
                    user_id,
                    seats_requested,
                    expires_at,
                    "active",
                    hold_nonce,
                    to_iso(utc_now()),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        return (
            {
                "hold_nonce": hold_nonce,
                "expires_at": expires_at,
                "seats_remaining": available_seats(get_db(), departure_id),
            },
            None,
            200,
        )

    def confirm_booking_for_user(user_id, hold_nonce, request_nonce, contact):
        ok, msg = assert_nonce(user_id, "booking_confirm", request_nonce)
        if not ok:
            log_risk("booking_nonce_failure", msg, user_id)
            return None, {"error": msg}, 409

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            cleanup_expired_holds(db)
            hold = db.execute(
                "SELECT * FROM seat_holds WHERE nonce=? AND user_id=? AND status='active'",
                (hold_nonce, user_id),
            ).fetchone()
            if not hold:
                db.rollback()
                return None, {"error": "Hold expired or invalid"}, 410

            departure = db.execute("SELECT * FROM departures WHERE id=?", (hold["departure_id"],)).fetchone()
            if available_seats(db, hold["departure_id"]) < hold["seats"]:
                db.rollback()
                return None, {"error": "Inventory conflict"}, 409

            total = price_for_departure(db, departure, hold["seats"])
            encrypted_contact = app.fernet.encrypt(contact.encode("utf-8")) if contact else None
            db.execute(
                "INSERT INTO bookings (departure_id,user_id,seats,total_price,status,contact_encrypted,nonce_used,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    hold["departure_id"],
                    user_id,
                    hold["seats"],
                    total,
                    "confirmed",
                    encrypted_contact,
                    request_nonce,
                    to_iso(utc_now()),
                ),
            )
            db.execute("UPDATE seat_holds SET status='converted' WHERE id=?", (hold["id"],))
            db.execute(
                "INSERT INTO analytics_events (user_id,event_type,created_at,metadata) VALUES (?,?,?,?)",
                (user_id, "booking_confirmed", to_iso(utc_now()), json.dumps({"departure_id": hold["departure_id"]})),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"ok": True, "total_price": total}, None, 200

    def assert_nonce(user_id, action, nonce):
        row = get_db().execute(
            "SELECT id,expires_at,used_at FROM sessions_nonce WHERE user_id=? AND action=? AND nonce=?",
            (user_id, action, nonce),
        ).fetchone()
        if not row:
            return False, "Invalid nonce"
        if row["used_at"]:
            return False, "Nonce already used"
        if from_iso(row["expires_at"]) < utc_now():
            return False, "Nonce expired"
        get_db().execute(
            "UPDATE sessions_nonce SET used_at=? WHERE id=?",
            (to_iso(utc_now()), row["id"]),
        )
        get_db().commit()
        return True, "ok"

    @app.before_request
    def security_guards():
        if (
            app.config["TLS_REQUIRED"]
            and not app.config["DISABLE_TLS_ENFORCEMENT"]
            and not app.testing
            and request.endpoint != "static"
        ):
            secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
            if not secure:
                abort(426, description="TLS is required")

        if session.get("user_id"):
            now = utc_now()
            last_seen = session.get("last_seen")
            if last_seen:
                idle = now - from_iso(last_seen)
                if idle > timedelta(minutes=30):
                    session.clear()
                    return redirect(url_for("login"))
            session["last_seen"] = to_iso(now)

        if request.endpoint in {"heartbeat", "arrival_board", "route_distribution", "search_departures"}:
            db = get_db()
            cleanup_expired_holds(db)
            db.commit()

    @app.get("/")
    def root():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            flash("Invalid credentials", "error")
            return redirect(url_for("login"))

        if user["lockout_until"] and from_iso(user["lockout_until"]) > utc_now():
            flash("Account temporarily locked for 15 minutes", "error")
            return redirect(url_for("login"))

        if not check_password_hash(user["password_hash"], password):
            failed = user["failed_attempts"] + 1
            lockout_until = None
            if failed >= 5:
                lockout_until = to_iso(utc_now() + timedelta(minutes=15))
                log_risk("failed_login_lockout", f"username={username}", user["id"])
                failed = 0
            db.execute(
                "UPDATE users SET failed_attempts=?, lockout_until=? WHERE id=?",
                (failed, lockout_until, user["id"]),
            )
            db.commit()
            flash("Invalid credentials", "error")
            return redirect(url_for("login"))

        db.execute("UPDATE users SET failed_attempts=0, lockout_until=NULL WHERE id=?", (user["id"],))
        db.commit()
        session.clear()
        session["user_id"] = user["id"]
        session["last_seen"] = to_iso(utc_now())
        session.permanent = True
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        user = current_user()
        routes = get_db().execute("SELECT * FROM routes ORDER BY code").fetchall()
        departures = get_db().execute(
            "SELECT d.id, r.code, d.departure_time FROM departures d JOIN routes r ON r.id=d.route_id ORDER BY d.departure_time LIMIT 10"
        ).fetchall()
        return render_template("dashboard.html", user=user, routes=routes, departures=departures)

    @app.get("/kiosk")
    def kiosk():
        routes = get_db().execute("SELECT * FROM routes ORDER BY code").fetchall()
        return render_template("kiosk.html", routes=routes)

    @app.get("/api/heartbeat")
    def heartbeat():
        user_id = session.get("user_id", 0)
        screen = request.args.get("screen", "unknown")
        now = utc_now()
        bucket = now.strftime("%Y-%m-%dT%H:%M")
        previous_bucket = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
        db = get_db()
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
        payload = request.get_json(force=True)
        response, error, status = create_booking_hold_for_user(session["user_id"], payload)
        if error:
            return jsonify(error), status
        return jsonify(response), status

    @app.post("/api/bookings/confirm")
    @login_required
    @require_permission("booking:create")
    def confirm_booking():
        payload = request.get_json(force=True)
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
        payload = request.get_json(force=True)
        kiosk_user_id = ensure_kiosk_user_id(get_db())
        response, error, status = create_booking_hold_for_user(kiosk_user_id, payload)
        if error:
            return jsonify(error), status
        return jsonify(response), status

    @app.post("/api/kiosk/bookings/confirm")
    def kiosk_confirm_booking():
        payload = request.get_json(force=True)
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
        for row in reader:
            vehicle_id = row.get("vehicle_id")
            route_raw = row.get("route_id")
            if not vehicle_id or route_raw is None:
                continue
            route_id = int(route_raw)
            stop_sequence = int(row.get("stop_sequence", 1) or 1)
            speed_mph = float(row.get("speed_mph", 0) or 0)
            ping_time = row.get("ping_time") or to_iso(utc_now())
            previous = db.execute(
                "SELECT speed_mph,ping_time FROM vehicle_pings WHERE vehicle_id=? ORDER BY ping_time DESC LIMIT 1",
                (vehicle_id,),
            ).fetchone()
            if previous:
                delta_hours = max(
                    1 / 3600,
                    (from_iso(ping_time) - from_iso(previous["ping_time"])).total_seconds() / 3600,
                )
                if abs(speed_mph - previous["speed_mph"]) / delta_hours > 85:
                    log_risk(
                        "impossible_speed_jump",
                        f"vehicle={vehicle_id}, from={previous['speed_mph']}, to={speed_mph}",
                        session["user_id"],
                    )
            db.execute(
                "INSERT INTO vehicle_pings (vehicle_id,route_id,stop_sequence,lat,lon,speed_mph,ping_time,source) VALUES (?,?,?,?,?,?,?,?)",
                (
                    vehicle_id,
                    route_id,
                    stop_sequence,
                    float(row.get("lat", 0)),
                    float(row.get("lon", 0)),
                    speed_mph,
                    ping_time,
                    "csv",
                ),
            )
            inserted += 1
        db.commit()
        return jsonify({"ok": True, "inserted": inserted})

    @app.post("/api/depot/bins/<int:bin_id>/freeze")
    @login_required
    @require_permission("depot:manage")
    def freeze_bin(bin_id):
        frozen = int(request.form.get("frozen", "1"))
        get_db().execute("UPDATE bins SET frozen=? WHERE id=?", (frozen, bin_id))
        get_db().commit()
        return jsonify({"ok": True, "frozen": bool(frozen)})

    @app.post("/api/depot/allocate")
    @login_required
    @require_permission("depot:manage")
    def allocate_inventory():
        payload = request.get_json(force=True)
        request_nonce = payload.get("request_nonce")
        ok, msg = assert_nonce(session["user_id"], "inventory_adjust", request_nonce)
        if not ok:
            return jsonify({"error": msg}), 409
        bin_id = int(payload["bin_id"])
        vol = float(payload["volume_cuft"])
        weight = float(payload["weight_lb"])

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

    @app.get("/notes")
    @login_required
    @require_permission("notes:read")
    def notes_index():
        user = current_user()
        if not user:
            abort(401)
        if is_note_manager(user):
            notes = get_db().execute(
                "SELECT n.id,n.title,n.note_type,n.updated_at,n.depot_scope,u.username owner FROM notes n JOIN users u ON u.id=n.owner_id ORDER BY n.updated_at DESC LIMIT 50"
            ).fetchall()
        else:
            notes = get_db().execute(
                "SELECT n.id,n.title,n.note_type,n.updated_at,n.depot_scope,u.username owner FROM notes n JOIN users u ON u.id=n.owner_id WHERE n.depot_scope=? ORDER BY n.updated_at DESC LIMIT 50",
                (user["depot_assignment"],),
            ).fetchall()
        return render_template("notes.html", notes=notes)

    @app.post("/api/notes")
    @login_required
    @require_permission("notes:write")
    def save_note():
        payload = request.get_json(force=True)
        note_id = payload.get("id")
        title = payload["title"].strip()
        content_md = payload.get("content_md", "")
        note_type = payload.get("note_type", "training")

        db = get_db()
        user = current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        now = to_iso(utc_now())
        if note_id:
            note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not note:
                return jsonify({"error": "Note not found"}), 404
            if not can_edit_note(user, note):
                return jsonify({"error": "Forbidden"}), 403
            version_no = db.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM note_versions WHERE note_id=?", (note_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO note_versions (note_id,version_no,title,content_md,created_by,created_at) VALUES (?,?,?,?,?,?)",
                (note_id, version_no, note["title"], note["content_md"], session["user_id"], now),
            )
            db.execute(
                "UPDATE notes SET title=?, content_md=?, updated_at=? WHERE id=?",
                (title, content_md, now, note_id),
            )
            db.execute(
                "DELETE FROM note_versions WHERE id IN (SELECT id FROM note_versions WHERE note_id=? ORDER BY version_no DESC LIMIT -1 OFFSET 20)",
                (note_id,),
            )
            db.commit()
            return jsonify({"ok": True, "id": note_id})

        cursor = db.execute(
            "INSERT INTO notes (title,content_md,note_type,owner_id,depot_scope,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (title, content_md, note_type, session["user_id"], user["depot_assignment"], now, now),
        )
        db.commit()
        return jsonify({"ok": True, "id": cursor.lastrowid})

    @app.post("/api/notes/<int:note_id>/attachments")
    @login_required
    @require_permission("notes:write")
    def upload_attachment(note_id):
        note = get_db().execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(current_user(), note):
            return jsonify({"error": "Forbidden"}), 403
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "File required"}), 400
        if request.content_length and request.content_length > 20 * 1024 * 1024:
            return jsonify({"error": "File too large"}), 413
        filename = secure_filename(file.filename or "attachment.bin")
        stored = ATTACHMENTS_DIR / f"{note_id}_{secrets.token_hex(4)}_{filename}"
        file.save(stored)
        size = stored.stat().st_size
        if size > 20 * 1024 * 1024:
            stored.unlink(missing_ok=True)
            return jsonify({"error": "File too large"}), 413
        get_db().execute(
            "INSERT INTO note_attachments (note_id,filename,stored_path,size_bytes,uploaded_by,uploaded_at) VALUES (?,?,?,?,?,?)",
            (note_id, filename, str(stored), size, session["user_id"], to_iso(utc_now())),
        )
        get_db().commit()
        return jsonify({"ok": True})

    @app.post("/api/notes/link")
    @login_required
    @require_permission("notes:write")
    def link_notes():
        payload = request.get_json(force=True)
        left = int(payload["from_note_id"])
        right = int(payload["to_note_id"])
        link_type = payload.get("link_type", "related")
        db = get_db()
        user = current_user()
        left_note = db.execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (left,)).fetchone()
        right_note = db.execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (right,)).fetchone()
        if not left_note or not right_note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(user, left_note) or not can_edit_note(user, right_note):
            return jsonify({"error": "Forbidden"}), 403
        db.execute(
            "INSERT OR IGNORE INTO note_links (from_note_id,to_note_id,link_type) VALUES (?,?,?)",
            (left, right, link_type),
        )
        db.execute(
            "INSERT OR IGNORE INTO note_links (from_note_id,to_note_id,link_type) VALUES (?,?,?)",
            (right, left, link_type),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.post("/api/notes/<int:note_id>/rollback/<int:version_no>")
    @login_required
    @require_permission("notes:write")
    def rollback_note(note_id, version_no):
        db = get_db()
        note = db.execute("SELECT owner_id,depot_scope FROM notes WHERE id=?", (note_id,)).fetchone()
        if not note:
            return jsonify({"error": "Note not found"}), 404
        if not can_edit_note(current_user(), note):
            return jsonify({"error": "Forbidden"}), 403
        version = db.execute(
            "SELECT title,content_md FROM note_versions WHERE note_id=? AND version_no=?",
            (note_id, version_no),
        ).fetchone()
        if not version:
            return jsonify({"error": "Version not found"}), 404
        db.execute(
            "UPDATE notes SET title=?, content_md=?, updated_at=? WHERE id=?",
            (version["title"], version["content_md"], to_iso(utc_now()), note_id),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/notes/rollup")
    @login_required
    def notes_rollup():
        db = get_db()
        user = current_user()
        if not user:
            return jsonify([])
        if is_note_manager(user):
            data = db.execute(
                """
                SELECT n.note_type, COUNT(*) AS total,
                    SUM(CASE WHEN nl.id IS NOT NULL THEN 1 ELSE 0 END) AS linked
                FROM notes n
                LEFT JOIN note_links nl ON nl.from_note_id=n.id
                GROUP BY n.note_type
                """
            ).fetchall()
        else:
            data = db.execute(
                """
                SELECT n.note_type, COUNT(*) AS total,
                    SUM(CASE WHEN nl.id IS NOT NULL THEN 1 ELSE 0 END) AS linked
                FROM notes n
                LEFT JOIN note_links nl ON nl.from_note_id=n.id
                WHERE n.depot_scope=?
                GROUP BY n.note_type
                """,
                (user["depot_assignment"],),
            ).fetchall()
        return jsonify([dict(row) for row in data])

    @app.post("/api/social/action")
    @login_required
    @require_permission("social:use")
    def social_action():
        payload = request.get_json(force=True)
        target_id = int(payload["target_user_id"])
        relation = payload["relation"]
        actor_id = session["user_id"]
        if target_id == actor_id:
            return jsonify({"error": "Cannot relate to self"}), 400

        db = get_db()
        if relation == "unfollow":
            db.execute(
                "DELETE FROM relationships WHERE user_a=? AND user_b=? AND relation='follow'",
                (actor_id, target_id),
            )
        else:
            db.execute(
                "INSERT OR IGNORE INTO relationships (user_a,user_b,relation,created_at) VALUES (?,?,?,?)",
                (actor_id, target_id, relation, to_iso(utc_now())),
            )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/profiles/<int:user_id>")
    @login_required
    def profile(user_id):
        viewer = session["user_id"]
        db = get_db()
        owner = db.execute(
            "SELECT id,username,role,depot_assignment,face_identifier_encrypted FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not owner:
            abort(404)
        blocked = db.execute(
            "SELECT 1 FROM relationships WHERE user_a=? AND user_b=? AND relation='block'",
            (owner["id"], viewer),
        ).fetchone()
        if blocked:
            abort(403)

        follow_a = db.execute(
            "SELECT 1 FROM relationships WHERE user_a=? AND user_b=? AND relation='follow'",
            (viewer, owner["id"]),
        ).fetchone()
        follow_b = db.execute(
            "SELECT 1 FROM relationships WHERE user_a=? AND user_b=? AND relation='follow'",
            (owner["id"], viewer),
        ).fetchone()
        mutual = bool(follow_a and follow_b)
        masked_face = ""
        if owner["face_identifier_encrypted"]:
            raw = app.fernet.decrypt(owner["face_identifier_encrypted"]).decode("utf-8")
            masked_face = mask_face_identifier(raw)
        return render_template("profile.html", owner=owner, mutual=mutual, masked_face=masked_face)

    def assign_variant(user_id, experiment_id):
        hash_key = hashlib.sha256(f"{user_id}:{experiment_id}".encode("utf-8")).hexdigest()
        return "A" if int(hash_key[-1], 16) % 2 == 0 else "B"

    @app.get("/api/experiments/assign/<widget_key>")
    @login_required
    def experiment_assign(widget_key):
        db = get_db()
        exp = db.execute("SELECT * FROM experiments WHERE widget_key=?", (widget_key,)).fetchone()
        if not exp or not exp["enabled"]:
            return jsonify({"variant": "A", "label": "Version A", "enabled": False})
        assignment = db.execute(
            "SELECT variant FROM experiment_assignments WHERE experiment_id=? AND user_id=?",
            (exp["id"], session["user_id"]),
        ).fetchone()
        if not assignment:
            variant = assign_variant(session["user_id"], exp["id"])
            db.execute(
                "INSERT INTO experiment_assignments (experiment_id,user_id,variant,created_at) VALUES (?,?,?,?)",
                (exp["id"], session["user_id"], variant, to_iso(utc_now())),
            )
            db.commit()
        else:
            variant = assignment["variant"]
        label = exp["label_a"] if variant == "A" else exp["label_b"]
        return jsonify({"variant": variant, "label": label, "enabled": True})

    @app.get("/supervisor/experiments")
    @login_required
    @require_permission("experiments:manage")
    def supervisor_experiments():
        exps = get_db().execute("SELECT * FROM experiments ORDER BY widget_key").fetchall()
        return render_template("experiments.html", experiments=exps)

    @app.post("/supervisor/experiments/<int:exp_id>/toggle")
    @login_required
    @require_permission("experiments:manage")
    def toggle_experiment(exp_id):
        enabled = int(request.form.get("enabled", "1"))
        get_db().execute("UPDATE experiments SET enabled=? WHERE id=?", (enabled, exp_id))
        get_db().commit()
        return redirect(url_for("supervisor_experiments"))

    @app.get("/analyst/metrics")
    @login_required
    @require_permission("analytics:view")
    def analyst_metrics():
        db = get_db()
        impressions = db.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE event_type='rec_impression'"
        ).fetchone()[0]
        clicks = db.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type='rec_click'").fetchone()[0]
        bookings = db.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE event_type='booking_confirmed'"
        ).fetchone()[0]

        seven_days_ago = to_iso(utc_now() - timedelta(days=7))
        returns = db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE created_at >= ? GROUP BY user_id HAVING COUNT(*) > 1",
            (seven_days_ago,),
        ).fetchall()
        return_usage = len(returns)

        rank = db.execute(
            """
            SELECT
              AVG(CASE WHEN recommended=1 AND relevant=1 THEN 1.0 ELSE 0 END) AS precision,
              AVG(CASE WHEN relevant=1 AND recommended=1 THEN 1.0 ELSE 0 END) AS recall,
              AVG(ndcg) AS ndcg,
              AVG(covered) AS coverage,
              AVG(diverse) AS diversity
            FROM ranking_samples
            """
        ).fetchone()

        ctr = (clicks / impressions) if impressions else 0
        conversion = (bookings / clicks) if clicks else 0
        return render_template(
            "metrics.html",
            ctr=round(ctr, 3),
            conversion=round(conversion, 3),
            return_usage=return_usage,
            rank=rank,
        )

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

    app.get_db = get_db
    app.init_db = init_db
    return app


app = create_app()
app.init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, ssl_context="adhoc")
