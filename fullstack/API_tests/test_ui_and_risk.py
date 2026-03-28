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


def authed_post(client, url, **kwargs):
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["X-CSRF-Token"] = token
    return client.post(url, headers=headers, **kwargs)


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


def test_arrival_board_threshold_edges(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)
    now = datetime.now(UTC)
    with app.app_context():
        app.get_db().execute(
            "INSERT INTO vehicle_pings (vehicle_id,route_id,stop_sequence,lat,lon,speed_mph,ping_time,source) VALUES (?,?,?,?,?,?,?,?)",
            ("EDGE-IN", 1, 1, 0.0, 0.0, 20.0, (now - timedelta(minutes=2) + timedelta(seconds=1)).isoformat(), "csv"),
        )
        app.get_db().execute(
            "INSERT INTO vehicle_pings (vehicle_id,route_id,stop_sequence,lat,lon,speed_mph,ping_time,source) VALUES (?,?,?,?,?,?,?,?)",
            ("EDGE-OUT", 2, 1, 0.0, 0.0, 20.0, (now - timedelta(minutes=2) - timedelta(seconds=1)).isoformat(), "csv"),
        )
        app.get_db().commit()

    route1 = client.get("/api/arrival-board?route_id=1")
    assert route1.status_code == 200
    assert b"Live" in route1.data

    route2 = client.get("/api/arrival-board?route_id=2")
    assert route2.status_code == 200
    assert b"Scheduled" in route2.data


def test_excessive_refresh_generates_risk_event(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    for _ in range(30):
        response = client.get("/api/heartbeat?screen=dashboard")
        assert response.status_code == 200
        with app.app_context():
            app.get_db().execute(
                "UPDATE refresh_cadence SET last_seen=? WHERE actor_key=? AND screen=?",
                ((datetime.now(UTC) - timedelta(seconds=11)).isoformat(), "user:1", "dashboard"),
            )
            app.get_db().commit()

    throttled = client.get("/api/heartbeat?screen=dashboard")
    assert throttled.status_code == 429

    with app.app_context():
        count = app.get_db().execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type='excessive_refresh'"
        ).fetchone()[0]
        assert count >= 1

    other_screen = client.get("/api/heartbeat?screen=kiosk")
    assert other_screen.status_code == 200


def test_strict_ten_second_refresh_cap(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)
    first = client.get("/api/heartbeat?screen=dashboard")
    assert first.status_code == 200
    second = client.get("/api/heartbeat?screen=dashboard")
    assert second.status_code == 429


def test_refresh_governance_applies_to_arrival_endpoint(tmp_path):
    client, _app = build_client(tmp_path)
    first = client.get("/api/arrival-board")
    assert first.status_code == 200
    second = client.get("/api/arrival-board")
    assert second.status_code == 429


def test_seat_availability_query_endpoint_and_refresh_cap(tmp_path):
    client, app = build_client(tmp_path)
    with app.app_context():
        dep_id = app.get_db().execute("SELECT id FROM departures ORDER BY id LIMIT 1").fetchone()[0]

    first = client.get(f"/api/seat-availability?departure_id={dep_id}&screen=dashboard-seat-availability")
    assert first.status_code == 200
    assert b"Seats remaining" in first.data
    second = client.get(f"/api/seat-availability?departure_id={dep_id}&screen=dashboard-seat-availability")
    assert second.status_code == 429


def test_dashboard_contains_htmx_seat_refresh(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert b"hx-trigger=\"load, every 10s, change from:#departure-select\"" in page.data
    assert b"screen=dashboard-seat-availability" in page.data


def test_depot_mutation_requires_permission(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    denied = authed_post(client, "/api/depot/bins/1/freeze", data={"frozen": "1"})
    assert denied.status_code == 403

    login_supervisor(client)
    allowed = authed_post(client, "/api/depot/bins/1/freeze", data={"frozen": "1"})
    assert allowed.status_code == 200

    nonce = authed_post(client, "/api/security/nonce", data={"action": "inventory_adjust"}).get_json()["nonce"]
    allocate = authed_post(
        client,
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

    freeze_back = authed_post(client, "/api/depot/bins/1/freeze", data={"frozen": "0"})
    assert freeze_back.status_code == 200
    nonce2 = authed_post(client, "/api/security/nonce", data={"action": "inventory_adjust"}).get_json()["nonce"]
    frozen_then_allocate = authed_post(
        client,
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


def test_depot_hierarchy_management_crud_and_validation(tmp_path):
    client, _app = build_client(tmp_path)
    login_supervisor(client)

    hierarchy = client.get("/api/depot/hierarchy")
    assert hierarchy.status_code == 200
    payload = hierarchy.get_json()
    assert "warehouses" in payload and "zones" in payload and "bins" in payload

    created_wh = authed_post(client, "/api/depot/warehouses", json={"name": "Overflow Depot"})
    assert created_wh.status_code == 201
    warehouse_id = created_wh.get_json()["id"]

    created_zone = authed_post(client, "/api/depot/zones", json={"warehouse_id": warehouse_id, "name": "Zone X"})
    assert created_zone.status_code == 201
    zone_id = created_zone.get_json()["id"]

    bad_bin = authed_post(
        client,
        "/api/depot/bins",
        json={
            "zone_id": zone_id,
            "code": "X-01",
            "bin_type": "invalid",
            "status": "available",
            "capacity_cuft": 10,
            "capacity_lb": 100,
        },
    )
    assert bad_bin.status_code == 422

    created_bin = authed_post(
        client,
        "/api/depot/bins",
        json={
            "zone_id": zone_id,
            "code": "X-01",
            "bin_type": "secure",
            "status": "maintenance",
            "capacity_cuft": 10,
            "capacity_lb": 100,
        },
    )
    assert created_bin.status_code == 201
    bin_id = created_bin.get_json()["id"]

    meta_bad = authed_post(client, f"/api/depot/bins/{bin_id}/metadata", json={"status": "bad"})
    assert meta_bad.status_code == 422
    meta_ok = authed_post(client, f"/api/depot/bins/{bin_id}/metadata", json={"status": "available", "bin_type": "cold"})
    assert meta_ok.status_code == 200

    manage_page = client.get("/depot/manage")
    assert manage_page.status_code == 200
    assert b"Depot Hierarchy Manager" in manage_page.data


def test_note_object_level_authorization(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)
    create = authed_post(
        client,
        "/api/notes",
        json={"title": "Agent Note", "content_md": "A", "note_type": "training"},
    )
    note_id = create.get_json()["id"]

    login_supervisor(client)
    denied = authed_post(
        client,
        "/api/notes",
        json={"id": note_id, "title": "Hijack", "content_md": "B", "note_type": "training"},
    )
    assert denied.status_code == 403

    with app.app_context():
        title = app.get_db().execute("SELECT title FROM notes WHERE id=?", (note_id,)).fetchone()[0]
        assert title == "Agent Note"

    login_hr(client)
    allowed = authed_post(
        client,
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

    create_local = authed_post(
        client,
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


def test_notes_page_rollup_ui_section_present(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)
    page = client.get("/notes")
    assert page.status_code == 200
    assert b"Cross-Task Rollups" in page.data
    assert b"/api/notes/rollup" in page.data


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


def test_session_inactivity_timeout_redirects(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)
    with client.session_transaction() as sess:
        sess["last_seen"] = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_admin_user_create_enforces_password_policy(tmp_path):
    client, _app = build_client(tmp_path)
    login_admin(client)

    weak = authed_post(
        client,
        "/admin/users",
        json={"username": "weakuser", "password": "short", "role": "employee", "depot_assignment": "Depot A"},
    )
    assert weak.status_code == 422

    strong = authed_post(
        client,
        "/admin/users",
        json={"username": "newagent", "password": "LongEnoughPass!9", "role": "employee", "depot_assignment": "Depot A"},
    )
    assert strong.status_code == 201

    duplicate = authed_post(
        client,
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
    upload = authed_post(
        client,
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

    note_a = authed_post(
        client,
        "/api/notes",
        json={
            "title": "N1",
            "content_md": "one\n<script>alert(1)</script>\n[bad](javascript:alert(2))",
            "note_type": "training",
        },
    ).get_json()["id"]
    note_b = authed_post(client, "/api/notes", json={"title": "N2", "content_md": "two", "note_type": "incident"}).get_json()["id"]

    preview = client.get(f"/api/notes/{note_a}/render")
    assert preview.status_code == 200
    html = preview.get_json()["html"]
    assert "<p>" in html or "<br>" in html
    assert "<script" not in html
    assert "javascript:" not in html

    link = authed_post(client, "/api/notes/link", json={"from_note_id": note_a, "to_note_id": note_b, "link_type": "related"})
    assert link.status_code == 200

    upd = authed_post(client, "/api/notes", json={"id": note_a, "title": "N1-v2", "content_md": "two", "note_type": "training"})
    assert upd.status_code == 200

    versions = client.get(f"/api/notes/{note_a}/versions")
    assert versions.status_code == 200
    versions_payload = versions.get_json()
    assert len(versions_payload) >= 1
    target_version = versions_payload[0]["version_no"]

    rollback = authed_post(client, f"/api/notes/{note_a}/rollback/{target_version}")
    assert rollback.status_code == 200

    attach = authed_post(
        client,
        f"/api/notes/{note_a}/attachments",
        data={"file": (BytesIO(b"hello"), "doc.txt")},
        content_type="multipart/form-data",
    )
    assert attach.status_code == 200

    follow = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "follow"})
    assert follow.status_code == 200
    block = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "block"})
    assert block.status_code == 200
    favorite = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "favorite"})
    assert favorite.status_code == 200
    like = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "like"})
    assert like.status_code == 200
    report = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "report"})
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


def test_geospatial_implied_speed_anomaly_detection(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    csv_data = (
        "vehicle_id,route_id,stop_sequence,speed_mph,ping_time,lat,lon\n"
        "V2,1,1,20,2026-01-01T00:00:00+00:00,40.0,-74.0\n"
        "V2,1,1,20,2026-01-01T00:01:00+00:00,41.0,-74.0\n"
    )
    upload = authed_post(
        client,
        "/api/vehicle-pings/upload",
        data={"file": (BytesIO(csv_data.encode("utf-8")), "pings_geo.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    with app.app_context():
        rows = app.get_db().execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type='impossible_speed_jump' AND details LIKE '%implied_mph=%'"
        ).fetchone()[0]
        assert rows >= 1


def test_lan_gateway_ingestion_and_csrf_protection(tmp_path):
    os.environ["METROOPS_GATEWAY_TOKEN"] = "gateway-secret"
    client, app = build_client(tmp_path)
    login_agent(client)

    missing_csrf = client.post("/api/notes", json={"title": "Should fail", "content_md": "x", "note_type": "training"})
    assert missing_csrf.status_code == 403

    bad_gateway = client.post("/api/vehicle-pings/gateway", json={"pings": []})
    assert bad_gateway.status_code == 403

    good_gateway = client.post(
        "/api/vehicle-pings/gateway",
        json={
            "pings": [
                {
                    "vehicle_id": "GW-1",
                    "route_id": 1,
                    "stop_sequence": 1,
                    "speed_mph": 22,
                    "ping_time": datetime.now(UTC).isoformat(),
                    "lat": 0,
                    "lon": 0,
                }
            ]
        },
        headers={"X-Gateway-Token": "gateway-secret"},
    )
    assert good_gateway.status_code == 200
    with app.app_context():
        row = app.get_db().execute(
            "SELECT source FROM vehicle_pings WHERE vehicle_id='GW-1' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["source"] == "lan_gateway"


def test_malformed_payloads_return_4xx_not_500(tmp_path):
    client, _app = build_client(tmp_path)
    login_supervisor(client)

    bad_relation = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "INVALID"})
    assert bad_relation.status_code == 422

    nonce = authed_post(client, "/api/security/nonce", data={"action": "inventory_adjust"}).get_json()["nonce"]
    bad_allocate = authed_post(
        client,
        "/api/depot/allocate",
        json={"request_nonce": nonce, "bin_id": "bad", "volume_cuft": "x", "weight_lb": "y"},
    )
    assert bad_allocate.status_code == 422


def test_attachment_size_limit_and_unauthorized_matrix(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)

    note_id = authed_post(
        client,
        "/api/notes",
        json={"title": "Big Attach", "content_md": "x", "note_type": "training"},
    ).get_json()["id"]

    oversized = authed_post(
        client,
        f"/api/notes/{note_id}/attachments",
        data={"file": (BytesIO(b"a" * (20 * 1024 * 1024 + 1)), "big.bin")},
        content_type="multipart/form-data",
    )
    assert oversized.status_code == 413

    exp_denied = client.get("/supervisor/experiments")
    assert exp_denied.status_code == 403
    metrics_denied = client.get("/analyst/metrics")
    assert metrics_denied.status_code == 403
    admin_denied = authed_post(
        client,
        "/admin/users",
        json={"username": "x", "password": "LongEnoughPass!9", "role": "employee", "depot_assignment": "A"},
    )
    assert admin_denied.status_code == 403

    logout_denied = client.post("/logout")
    assert logout_denied.status_code == 403


def test_blocked_profile_visibility_denied(tmp_path):
    client, _app = build_client(tmp_path)
    login_agent(client)
    block = authed_post(client, "/api/social/action", json={"target_user_id": 2, "relation": "block"})
    assert block.status_code == 200

    login_supervisor(client)
    denied = client.get("/profiles/1")
    assert denied.status_code == 403
