import importlib
import os
from io import BytesIO
from datetime import UTC, datetime, timedelta


def build_client(tmp_path):
    os.environ["METROOPS_DB_PATH"] = str(tmp_path / "metroops_api.db")
    os.environ["METROOPS_KEY_PATH"] = str(tmp_path / "metroops_api.key")
    os.environ["DISABLE_TLS_ENFORCEMENT"] = "1"
    module = importlib.import_module("app.app")
    module = importlib.reload(module)
    app = module.create_app()
    app.testing = True
    app.config["DISABLE_TLS_ENFORCEMENT"] = True
    app.init_db()
    return app.test_client(), app


def login_agent(client):
    response = client.post(
        "/login",
        data={"username": "agent01", "password": "MetroOpsPass!01"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def login_supervisor(client):
    response = client.post(
        "/login",
        data={"username": "supervisor01", "password": "MetroOpsPass!02"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def login_hr(client):
    response = client.post(
        "/login",
        data={"username": "hr01", "password": "MetroOpsPass!03"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def login_admin(client):
    response = client.post(
        "/login",
        data={"username": "admin01", "password": "MetroOpsPass!04"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_arrival_board_scheduled_fallback(tmp_path):
    client, _app = build_client(tmp_path)
    page = client.get("/api/arrival-board")
    assert page.status_code == 200
    assert b"Scheduled" in page.data
    assert b"Last updated at" in page.data


def test_arrival_board_live_mode_with_recent_ping(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)
    now = datetime.now(UTC).isoformat()
    with app.app_context():
        app.get_db().execute(
            "INSERT INTO vehicle_pings (vehicle_id,route_id,stop_sequence,lat,lon,speed_mph,ping_time,source) VALUES (?,?,?,?,?,?,?,?)",
            ("LIVE-1", 1, 1, 0.0, 0.0, 25.0, now, "csv"),
        )
        app.get_db().commit()

    page = client.get("/api/arrival-board")
    assert page.status_code == 200
    assert b"Live" in page.data


def test_excessive_refresh_generates_risk_event(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    for _ in range(30):
        response = client.get("/api/heartbeat?screen=dashboard")
        assert response.status_code == 200

    throttled = client.get("/api/heartbeat?screen=dashboard")
    assert throttled.status_code == 429

    with app.app_context():
        count = app.get_db().execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type='excessive_refresh'"
        ).fetchone()[0]
        assert count >= 1

    other_screen = client.get("/api/heartbeat?screen=kiosk")
    assert other_screen.status_code == 200


def test_depot_mutation_requires_permission(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    denied = client.post("/api/depot/bins/1/freeze", data={"frozen": "1"})
    assert denied.status_code == 403

    login_supervisor(client)
    allowed = client.post("/api/depot/bins/1/freeze", data={"frozen": "1"})
    assert allowed.status_code == 200

    nonce = client.post("/api/security/nonce", data={"action": "inventory_adjust"}).get_json()["nonce"]
    allocate = client.post(
        "/api/depot/allocate",
        json={
            "request_nonce": nonce,
            "bin_id": 1,
            "item_name": "Crate",
            "volume_cuft": 1,
            "weight_lb": 1,
        },
    )
    assert allocate.status_code == 409

    freeze_back = client.post("/api/depot/bins/1/freeze", data={"frozen": "0"})
    assert freeze_back.status_code == 200
    nonce2 = client.post("/api/security/nonce", data={"action": "inventory_adjust"}).get_json()["nonce"]
    frozen_then_allocate = client.post(
        "/api/depot/allocate",
        json={
            "request_nonce": nonce2,
            "bin_id": 1,
            "item_name": "Crate2",
            "volume_cuft": 1,
            "weight_lb": 1,
        },
    )
    assert frozen_then_allocate.status_code == 200


def test_note_object_level_authorization(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)
    create = client.post(
        "/api/notes",
        json={"title": "Agent Note", "content_md": "A", "note_type": "training"},
    )
    note_id = create.get_json()["id"]

    login_supervisor(client)
    denied = client.post(
        "/api/notes",
        json={"id": note_id, "title": "Hijack", "content_md": "B", "note_type": "training"},
    )
    assert denied.status_code == 403

    with app.app_context():
        title = app.get_db().execute("SELECT title FROM notes WHERE id=?", (note_id,)).fetchone()[0]
        assert title == "Agent Note"

    login_hr(client)
    allowed = client.post(
        "/api/notes",
        json={"id": note_id, "title": "HR Override", "content_md": "C", "note_type": "training"},
    )
    assert allowed.status_code == 200


def test_kiosk_booking_hold_and_confirm(tmp_path):
    client, app = build_client(tmp_path)

    with app.app_context():
        dep_id = app.get_db().execute("SELECT id FROM departures ORDER BY id LIMIT 1").fetchone()[0]
        app.get_db().execute(
            "UPDATE departures SET departure_time=? WHERE id=?",
            ((datetime.now(UTC) + timedelta(hours=3)).isoformat(), dep_id),
        )
        app.get_db().commit()

    hold = client.post(
        "/api/kiosk/bookings/hold",
        json={"departure_id": dep_id, "seats": 1, "product_type": "single", "bundle_days": 1},
    )
    assert hold.status_code == 200
    hold_nonce = hold.get_json()["hold_nonce"]

    nonce = client.post("/api/kiosk/security/nonce", data={"action": "booking_confirm"})
    assert nonce.status_code == 200

    confirm = client.post(
        "/api/kiosk/bookings/confirm",
        json={
            "hold_nonce": hold_nonce,
            "request_nonce": nonce.get_json()["nonce"],
            "contact": "kiosk@rider.local",
        },
    )
    assert confirm.status_code == 200
    assert confirm.get_json()["ok"] is True


def test_notes_depot_scope_isolation(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    create_local = client.post(
        "/api/notes",
        json={"title": "Main Depot SOP", "content_md": "Local", "note_type": "training"},
    )
    assert create_local.status_code == 200

    with app.app_context():
        db = app.get_db()
        owner_id = db.execute("SELECT id FROM users WHERE username='agent01'").fetchone()[0]
        db.execute(
            "INSERT INTO notes (title,content_md,note_type,owner_id,depot_scope,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("Remote Only Note", "Hidden", "incident", owner_id, "Remote Depot", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        db.commit()

    page = client.get("/notes")
    assert page.status_code == 200
    assert b"Main Depot SOP" in page.data
    assert b"Remote Only Note" not in page.data


def test_reports_page_access_and_content(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)
    denied = client.get("/reports")
    assert denied.status_code == 403

    login_hr(client)
    allowed = client.get("/reports")
    assert allowed.status_code == 200
    assert b"Governance Reports" in allowed.data
    assert b"Risk Event Breakdown" in allowed.data


def test_admin_user_create_enforces_password_policy(tmp_path):
    client, _app = build_client(tmp_path)
    login_admin(client)

    weak = client.post(
        "/admin/users",
        json={"username": "weakuser", "password": "short", "role": "employee", "depot_assignment": "Depot A"},
    )
    assert weak.status_code == 422

    strong = client.post(
        "/admin/users",
        json={"username": "newagent", "password": "LongEnoughPass!9", "role": "employee", "depot_assignment": "Depot A"},
    )
    assert strong.status_code == 201

    duplicate = client.post(
        "/admin/users",
        json={"username": "newagent", "password": "LongEnoughPass!9", "role": "employee", "depot_assignment": "Depot A"},
    )
    assert duplicate.status_code == 409


def test_tls_enforcement_login_lockout_experiment_and_analytics(tmp_path):
    os.environ["METROOPS_DB_PATH"] = str(tmp_path / "metroops_tls.db")
    os.environ["METROOPS_KEY_PATH"] = str(tmp_path / "metroops_tls.key")
    os.environ["DISABLE_TLS_ENFORCEMENT"] = "0"
    module = importlib.import_module("app.app")
    module = importlib.reload(module)
    app = module.create_app()
    app.testing = False
    app.config["DISABLE_TLS_ENFORCEMENT"] = False
    app.init_db()
    client = app.test_client()

    no_tls = client.get("/login")
    assert no_tls.status_code == 426

    secure = client.post("/login", data={"username": "agent01", "password": "wrong-password"}, base_url="https://localhost")
    assert secure.status_code == 302
    for _ in range(4):
        resp = client.post("/login", data={"username": "agent01", "password": "wrong-password"}, base_url="https://localhost")
        assert resp.status_code == 302
    locked = client.post("/login", data={"username": "agent01", "password": "MetroOpsPass!01"}, base_url="https://localhost")
    assert locked.status_code == 302

    with app.app_context():
        lockout_until = app.get_db().execute(
            "SELECT lockout_until FROM users WHERE username='agent01'"
        ).fetchone()[0]
        assert lockout_until is not None
        assert datetime.fromisoformat(lockout_until) > datetime.now(UTC)

    supervisor_login = client.post(
        "/login",
        data={"username": "supervisor01", "password": "MetroOpsPass!02"},
        base_url="https://localhost",
    )
    assert supervisor_login.status_code == 302

    assign = client.get("/api/experiments/assign/suggested-times", base_url="https://localhost")
    assert assign.status_code == 200
    payload = assign.get_json()
    assert payload["variant"] in ("A", "B")
    assert payload["label"] in ("Version A", "Version B")

    with app.app_context():
        db = app.get_db()
        now = datetime.now(UTC).isoformat()
        db.execute("INSERT INTO analytics_events (user_id,event_type,created_at,metadata) VALUES (?,?,?,?)", (2, "rec_impression", now, "{}"))
        db.execute("INSERT INTO analytics_events (user_id,event_type,created_at,metadata) VALUES (?,?,?,?)", (2, "rec_click", now, "{}"))
        db.execute("INSERT INTO analytics_events (user_id,event_type,created_at,metadata) VALUES (?,?,?,?)", (2, "booking_confirmed", now, "{}"))
        db.execute("INSERT INTO ranking_samples (relevant,recommended,ndcg,covered,diverse,created_at) VALUES (?,?,?,?,?,?)", (1, 1, 0.9, 1, 1, now))
        db.commit()

    metrics = client.get("/analyst/metrics", base_url="https://localhost")
    assert metrics.status_code == 200
    assert b"CTR" in metrics.data
    assert b"1.0" in metrics.data


def test_anomaly_notes_features_social_and_masking(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    csv_data = "vehicle_id,route_id,stop_sequence,speed_mph,ping_time,lat,lon\nV1,1,1,10,2026-01-01T00:00:00+00:00,0,0\nV1,1,1,100,2026-01-01T00:01:00+00:00,0,0\n"
    upload = client.post(
        "/api/vehicle-pings/upload",
        data={"file": (BytesIO(csv_data.encode("utf-8")), "pings.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    with app.app_context():
        risk_count = app.get_db().execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type='impossible_speed_jump'"
        ).fetchone()[0]
        assert risk_count >= 1

    note_a = client.post("/api/notes", json={"title": "N1", "content_md": "one", "note_type": "training"}).get_json()["id"]
    note_b = client.post("/api/notes", json={"title": "N2", "content_md": "two", "note_type": "incident"}).get_json()["id"]
    link = client.post("/api/notes/link", json={"from_note_id": note_a, "to_note_id": note_b, "link_type": "related"})
    assert link.status_code == 200

    upd = client.post("/api/notes", json={"id": note_a, "title": "N1-v2", "content_md": "two", "note_type": "training"})
    assert upd.status_code == 200
    rollback = client.post(f"/api/notes/{note_a}/rollback/1")
    assert rollback.status_code == 200

    attach = client.post(
        f"/api/notes/{note_a}/attachments",
        data={"file": (BytesIO(b"hello"), "doc.txt")},
        content_type="multipart/form-data",
    )
    assert attach.status_code == 200

    follow = client.post("/api/social/action", json={"target_user_id": 2, "relation": "follow"})
    assert follow.status_code == 200
    block = client.post("/api/social/action", json={"target_user_id": 2, "relation": "block"})
    assert block.status_code == 200
    favorite = client.post("/api/social/action", json={"target_user_id": 2, "relation": "favorite"})
    assert favorite.status_code == 200
    like = client.post("/api/social/action", json={"target_user_id": 2, "relation": "like"})
    assert like.status_code == 200
    report = client.post("/api/social/action", json={"target_user_id": 2, "relation": "report"})
    assert report.status_code == 200

    with app.app_context():
        rel_count = app.get_db().execute(
            "SELECT COUNT(*) FROM relationships WHERE user_a=1 AND user_b=2 AND relation IN ('follow','block','favorite','like','report')"
        ).fetchone()[0]
        assert rel_count == 5

    with app.app_context():
        encrypted = app.fernet.encrypt(b"FACE123456")
        app.get_db().execute("UPDATE users SET face_identifier_encrypted=? WHERE id=?", (encrypted, 1))
        app.get_db().execute(
            "INSERT OR IGNORE INTO relationships (user_a,user_b,relation,created_at) VALUES (?,?,?,?)",
            (2, 1, "follow", datetime.now(UTC).isoformat()),
        )
        app.get_db().execute(
            "INSERT OR IGNORE INTO relationships (user_a,user_b,relation,created_at) VALUES (?,?,?,?)",
            (1, 2, "follow", datetime.now(UTC).isoformat()),
        )
        app.get_db().commit()

    profile = client.get("/profiles/1")
    assert profile.status_code == 200
    assert b"***" in profile.data
