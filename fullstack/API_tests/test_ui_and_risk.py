import importlib
import os
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


def test_arrival_board_scheduled_fallback(tmp_path):
    client, _app = build_client(tmp_path)
    page = client.get("/api/arrival-board")
    assert page.status_code == 200
    assert b"Scheduled" in page.data
    assert b"Last updated at" in page.data


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
    assert allocate.status_code in (200, 409)


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
