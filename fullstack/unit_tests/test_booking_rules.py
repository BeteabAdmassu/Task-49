import importlib
import os
from datetime import UTC, datetime, timedelta


def build_client(tmp_path):
    os.environ["METROOPS_DB_PATH"] = str(tmp_path / "metroops_test.db")
    os.environ["METROOPS_KEY_PATH"] = str(tmp_path / "metroops_test.key")
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


def test_booking_window_and_inventory_lock(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    with app.app_context():
        db = app.get_db()
        dep_id = db.execute("SELECT id FROM departures ORDER BY id LIMIT 1").fetchone()[0]
        db.execute(
            "UPDATE departures SET departure_time=?, total_seats=1 WHERE id=?",
            ((datetime.now(UTC) + timedelta(hours=3)).isoformat(), dep_id),
        )
        db.commit()

    first_hold = client.post("/api/bookings/hold", json={"departure_id": dep_id, "seats": 1})
    assert first_hold.status_code == 200

    second_hold = client.post("/api/bookings/hold", json={"departure_id": dep_id, "seats": 1})
    assert second_hold.status_code == 409
    assert second_hold.get_json()["error"] == "Insufficient seats"

    with app.app_context():
        db = app.get_db()
        db.execute(
            "UPDATE departures SET departure_time=? WHERE id=?",
            ((datetime.now(UTC) + timedelta(days=31)).isoformat(), dep_id),
        )
        db.commit()

    out_of_window = client.post("/api/bookings/hold", json={"departure_id": dep_id, "seats": 1})
    assert out_of_window.status_code == 422
    assert "more than 30 days" in out_of_window.get_json()["error"]


def test_booking_confirm_nonce_and_bundle_rule(tmp_path):
    client, app = build_client(tmp_path)
    login_agent(client)

    with app.app_context():
        db = app.get_db()
        dep_id = db.execute("SELECT id FROM departures ORDER BY id LIMIT 1").fetchone()[0]
        db.execute(
            "UPDATE departures SET departure_time=? WHERE id=?",
            ((datetime.now(UTC) + timedelta(hours=4)).isoformat(), dep_id),
        )
        db.commit()

    invalid_bundle = client.post(
        "/api/bookings/hold",
        json={"departure_id": dep_id, "seats": 1, "product_type": "commuter_bundle", "bundle_days": 2},
    )
    assert invalid_bundle.status_code == 422

    hold = client.post(
        "/api/bookings/hold",
        json={"departure_id": dep_id, "seats": 1, "product_type": "commuter_bundle", "bundle_days": 3},
    )
    hold_json = hold.get_json()
    nonce_res = client.post("/api/security/nonce", data={"action": "booking_confirm"})
    nonce = nonce_res.get_json()["nonce"]
    confirm = client.post(
        "/api/bookings/confirm",
        json={"hold_nonce": hold_json["hold_nonce"], "request_nonce": nonce, "contact": "rider@example.com"},
    )
    assert confirm.status_code == 200
    assert confirm.get_json()["ok"] is True

    replay = client.post(
        "/api/bookings/confirm",
        json={"hold_nonce": hold_json["hold_nonce"], "request_nonce": nonce, "contact": "rider@example.com"},
    )
    assert replay.status_code == 409
