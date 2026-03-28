import importlib
import os
import threading
from contextlib import contextmanager

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@contextmanager
def run_server(app, host="127.0.0.1", port=5015):
    from werkzeug.serving import make_server

    server = make_server(host, port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def build_app(tmp_path):
    os.environ["METROOPS_DB_PATH"] = str(tmp_path / "metroops_browser.db")
    os.environ["METROOPS_KEY_PATH"] = str(tmp_path / "metroops_browser.key")
    os.environ["DISABLE_TLS_ENFORCEMENT"] = "1"
    module = importlib.import_module("app.app")
    module = importlib.reload(module)
    app = module.create_app()
    app.testing = True
    app.config["DISABLE_TLS_ENFORCEMENT"] = True
    app.init_db()
    return app


def test_offline_banner_visibility_and_recovery(tmp_path):
    app = build_app(tmp_path)
    with run_server(app) as base_url:
        with playwright.sync_api.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{base_url}/kiosk")

            page.wait_for_selector("text=Last updated at")
            assert page.locator("text=Last updated at").first.is_visible()

            page.route("**/api/arrival-board**", lambda route: route.abort())
            page.evaluate("htmx.ajax('GET', '/api/arrival-board', 'section.panel:nth-of-type(3) div')")
            page.wait_for_timeout(200)
            text_from_htmx_failure = page.locator("#offline-banner").inner_text()
            assert "Offline" in text_from_htmx_failure
            assert page.locator("text=Last updated at").first.is_visible()

            page.unroute("**/api/arrival-board**")

            page.route("**/api/heartbeat**", lambda route: route.abort())
            page.evaluate("heartbeat()")
            page.wait_for_timeout(200)
            text = page.locator("#offline-banner").inner_text()
            assert "Offline" in text
            assert page.locator("text=Last updated at").first.is_visible()

            page.unroute("**/api/heartbeat**")
            page.route(
                "**/api/heartbeat**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"ok": true, "time": "3:12 PM"}',
                ),
            )
            page.evaluate("heartbeat()")
            page.wait_for_timeout(200)
            is_hidden = page.evaluate("document.getElementById('offline-banner').classList.contains('hidden')")
            assert is_hidden is True
            assert page.locator("text=Last updated at").first.is_visible()

            browser.close()
