import base64
from concurrent.futures import Future
from contextlib import closing
from copy import deepcopy
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import integrations
from localization import Text
from server import DashboardServer, Sessions, StatusPoller, Store, clean_status, fingerprint, tls_context


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jCMcAAAAASUVORK5CYII=")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="4" cy="4" r="4"/></svg>'
SEED = {
    "settings": {"title": "Test-Landingpage", "subtitle": "Übersicht"},
    "groups": [{"id": "network", "name": "Netzwerk", "items": [
        {"id": "dns", "name": "Pi-hole", "url": "http://192.0.2.10/", "kind": "pihole", "source": "dns-primary",
         "logo": "/assets/logos/DNS.svg"},
        {"id": "help", "name": "Hilfe", "url": "/hilfe/example.html", "kind": "link"},
    ]}],
}
MUTATION = {"X-Dashboard-Request": "1"}


class Provider:
    def __init__(self):
        self.visits = []
        self.updated = threading.Event()

    def sources(self):
        return [{"id": "dns-primary", "kind": "pihole", "label": "Primär", "secret": "internal"}]

    def probe(self, item):
        self.visits.append(item["id"])
        self.updated.set()
        return {
            "state": "ok", "label": "Aktiv",
            "metrics": [{"label": "Anfragen", "value": 172}],
            "observed_at": int(time.time()), "progress": 20.5,
        }


class DashboardTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.seed_dir = self.root / "seed"
        self.data_dir = self.root / "data"
        self.web_dir = self.root / "web"
        (self.seed_dir / "assets" / "logos").mkdir(parents=True)
        (self.seed_dir / "assets" / "logos" / "DNS.svg").write_bytes(SVG)
        (self.seed_dir / "seed.json").write_text(json.dumps(SEED))
        (self.web_dir / "hilfe").mkdir(parents=True)
        (self.web_dir / "index.html").write_text("<!doctype html><title>Dashboard</title>")
        (self.web_dir / "hilfe" / "example.js").write_text("document.body.dataset.loaded = '1'")
        self.store = Store(self.data_dir, self.seed_dir)
        self.provider = Provider()
        self.poller = StatusPoller(self.store, self.provider)
        self.addCleanup(self.poller.close)
        self.sessions = Sessions(b"synthetic-test-password", secure=False)
        self.http = self.listener(sessions=self.sessions)

    def listener(self, **kwargs):
        listener = DashboardServer(
            ("127.0.0.1", 0), store=self.store, poller=self.poller, web_dir=self.web_dir, **kwargs,
        )
        thread = threading.Thread(target=listener.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def close_listener():
            listener.shutdown()
            thread.join(2)
            listener.server_close()

        self.addCleanup(close_listener)
        return listener

    def send(self, method, path, payload=None, *, headers=None, listener=None, context=None, cookie=None):
        listener = listener or self.http
        if context is None:
            connection = HTTPConnection("127.0.0.1", listener.server_port, timeout=3)
        else:
            connection = HTTPSConnection("127.0.0.1", listener.server_port, context=context, timeout=3)
        request_headers = dict(headers or {})
        if isinstance(payload, dict):
            payload = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if cookie:
            request_headers["Cookie"] = cookie
        with closing(connection):
            connection.request(method, path, payload, request_headers)
            response = connection.getresponse()
            body = response.read()
            if response.getheader("Content-Type", "").startswith("application/json") and body:
                body = json.loads(body)
            return response.status, response.headers, body

    def login(self, **kwargs):
        headers = kwargs.pop("headers", MUTATION)
        code, response_headers, body = self.send(
            "POST", "/api/login", {"password": "synthetic-test-password"}, headers=headers, **kwargs,
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(body, {"authenticated": True})
        return response_headers["Set-Cookie"]

    def test_dashboard_static_assets_sources_and_health(self):
        code, headers, dashboard = self.send("GET", "/api/dashboard")
        self.assertEqual((code, dashboard), (200, {"revision": 1, **SEED, "authenticated": False}))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.send("GET", "/")[1]["Referrer-Policy"], "same-origin")
        self.assertEqual(self.send("GET", "/healthz")[2], {"ok": True})
        self.assertEqual(self.send("GET", "/api/sources")[2], {"sources": [
            {"id": "dns-primary", "kind": "pihole", "label": "Primär"},
        ]})
        code, headers, body = self.send("GET", "/assets/logos/DNS.svg")
        self.assertEqual((code, body), (200, SVG))
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertEqual(self.send("GET", "/assets/%2e%2e/server.py")[0], 404)
        code, headers, body = self.send("GET", "/hilfe/example.js")
        self.assertEqual(code, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.send("HEAD", "/")[2], b"")

    def test_sessions_and_mutation_protection(self):
        payload = {"password": "synthetic-test-password"}
        self.assertEqual(self.send("POST", "/api/login", payload)[0], 403)
        self.assertEqual(self.send(
            "POST", "/api/login", payload, headers={**MUTATION, "Origin": "http://example.invalid"},
        )[0], 403)
        self.assertEqual(self.send("POST", "/api/login", {"password": "wrong"}, headers=MUTATION)[0], 401)
        self.assertEqual(self.send("GET", "/api/backup")[0], 401)
        cookie_header = self.login(headers={**MUTATION, "Origin": f"http://127.0.0.1:{self.http.server_port}"})
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Strict", cookie_header)
        cookie = cookie_header.split(";", 1)[0]
        self.assertTrue(self.send("GET", "/api/dashboard", cookie=cookie)[2]["authenticated"])
        result = self.send("POST", "/api/logout", {}, cookie=cookie, headers=MUTATION)
        self.assertEqual(result[0], 200)
        self.assertIn("Max-Age=0", result[1]["Set-Cookie"])
        self.assertFalse(self.send("GET", "/api/dashboard", cookie=cookie)[2]["authenticated"])

    def test_http_language_is_selected_for_each_request(self):
        english = {"Accept-Language": "fr, en-GB;q=0.9, de;q=0.8"}
        german = {"Accept-Language": "en;q=0, de-AT;q=0.7"}
        item = SEED["groups"][0]["items"][0]
        stamp = int(time.time())
        reading = {
            "state": "ok", "label": "Primär", "detail": Text("Dienst antwortet", "Service is responding"),
            "metrics": [
                {"label": Text("Anfragen", "Queries", key="queries"), "value": Text("12.345", "12,345")},
                {"label": "Antwort", "value": "Aktiv"},
            ],
        }
        self.store.write_status(item["id"], fingerprint(item), clean_status(reading, stamp), stamp, 180)
        code, headers, body = self.send("GET", "/api/status", headers=english)
        self.assertEqual((code, headers["Content-Language"], headers["Vary"]), (200, "en", "Accept-Language"))
        status = body["items"][item["id"]]
        self.assertEqual((status["label"], status["detail"]), ("Primär", "Service is responding"))
        self.assertEqual(status["metrics"], [
            {"key": "queries", "label": "Queries", "value": "12,345"},
            {"label": "Antwort", "value": "Aktiv"},
        ])
        _, _, body = self.send("GET", "/api/status", headers=german)
        self.assertEqual(body["items"][item["id"]]["metrics"][0],
                         {"key": "queries", "label": "Anfragen", "value": "12.345"})
        _, _, body = self.send("GET", "/api/dashboard", headers=english)
        self.assertEqual(body["settings"], SEED["settings"])
        for header, lang, message in ((english, "en", "Sign-in required."), (german, "de", "Anmeldung erforderlich.")):
            code, headers, body = self.send("GET", "/api/backup", headers=header)
            self.assertEqual((code, headers["Content-Language"], body["error"]), (401, lang, message))
            self.assertEqual(body["error_translations"][lang], message)

    def test_public_channels_editor_flow(self):
        channel = {"id": "UC" + "x" * 22, "name": "Testkanal"}
        payload = {"input": "@Testkanal"}
        route = "/api/youtube/resolve"
        cookie = self.login().split(";", 1)[0]
        with patch("public_youtube.resolve_channel", return_value=channel) as resolve:
            self.assertEqual(self.send("POST", route, payload, headers=MUTATION)[0], 401)
            self.assertEqual(self.send("POST", route, payload, cookie=cookie)[0], 403)
            resolve.assert_not_called()
            response = self.send("POST", route, payload, headers=MUTATION, cookie=cookie)
            self.assertEqual((response[0], response[2]), (200, {"channel": channel}))

        doc = self.store.dashboard()
        item = doc["groups"][0]["items"][1]
        item.update(kind="youtube", channels=[channel])
        def save():
            return self.send("PUT", "/api/dashboard", doc, headers=MUTATION, cookie=cookie)

        item["channels"].append(channel)
        self.assertEqual(save()[0], 400)
        item["channels"].pop()
        code, _, result = save()
        self.assertEqual(code, 200, result)
        self.assertEqual(result["groups"][0]["items"][1]["channels"], [channel])
        self.assertIn(item["id"], self.poller.snapshot()["items"])
        doc["revision"] = result["revision"]
        item["channels"] = []
        self.assertEqual(save()[0], 200)
        self.assertNotIn(item["id"], self.poller.snapshot()["items"])

    def test_latest_video_id_in_status(self):
        metric = {"label": "Neuester Upload", "value": "Beispielvideo"}
        reading = {"state": "ok", "label": "Aktuell", "metrics": [metric]}
        now = int(time.time())
        for video_id in ("a_bcDe-Fg01", "", "kein/video", None):
            status = clean_status({**reading, "latest_video_id": video_id}, now)
            self.assertEqual(status["metrics"], [metric])
            if video_id == "a_bcDe-Fg01":
                self.assertEqual(status["latest_video_id"], video_id)
            else:
                self.assertNotIn("latest_video_id", status)

    def test_revision_and_restart(self):
        cookie = self.login().split(";", 1)[0]
        data = {"revision": 1, **deepcopy(SEED)}
        data["settings"]["title"] = "Neue Übersicht"
        barrier = threading.Barrier(2)
        results = []

        def writer():
            barrier.wait(2)
            results.append(self.send("PUT", "/api/dashboard", data, cookie=cookie, headers=MUTATION))

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        conflict = next(result for result in results if result[0] == 409)
        self.assertEqual(conflict[2]["revision"], 2)
        reopened = Store(self.data_dir, self.seed_dir)
        self.assertEqual(reopened.dashboard(), {**data, "revision": 2})
        self.assertEqual(reopened.asset("logos/DNS.svg"), SVG)

    def test_validation_failure_keeps_revision(self):
        cookie = self.login().split(";", 1)[0]
        invalid = {"revision": 1, **deepcopy(SEED)}
        for url in ("javascript:placeholder", "//untrusted.invalid", "/%2Fexample.invalid", "https://u:p@example.invalid/"):
            with self.subTest(url=url):
                invalid["groups"][0]["items"][0]["url"] = url
                self.assertEqual(self.send("PUT", "/api/dashboard", invalid, cookie=cookie, headers=MUTATION)[0], 400)
        invalid["groups"][0]["items"][0]["url"] = "/hilfe/example.html"
        invalid["groups"][0]["items"][0]["id"] = "help"
        self.assertEqual(self.send("PUT", "/api/dashboard", invalid, cookie=cookie, headers=MUTATION)[0], 400)
        self.assertEqual(self.store.dashboard(), {"revision": 1, **SEED})

    def test_backup_restore_round_trip_and_asset_replacement(self):
        cookie = self.login().split(";", 1)[0]
        code, headers, backup = self.send("GET", "/api/backup", cookie=cookie)
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        with ZipFile(BytesIO(backup)) as archive:
            self.assertEqual(json.loads(archive.read("dashboard.json")), {
                "schema_version": 1, "dashboard": {"revision": 1, **SEED},
            })
            self.assertEqual(archive.read("assets/logos/DNS.svg"), SVG)
        modified = deepcopy(SEED)
        modified["settings"]["title"] = "Sicherung"
        altered = self.archive(modified, {"assets/new.png": PNG})
        restore_headers = {**MUTATION, "Content-Type": "application/zip"}
        code, _, body = self.send("POST", "/api/restore", altered, cookie=cookie, headers=restore_headers)
        self.assertEqual((code, body), (200, {"revision": 2, **modified, "authenticated": True}))
        self.assertEqual(self.send("GET", "/assets/logos/DNS.svg")[0], 404)
        self.assertEqual(self.send("GET", "/assets/new.png")[2], PNG)
        code, _, body = self.send("POST", "/api/restore", backup, cookie=cookie, headers=restore_headers)
        self.assertEqual((code, body), (200, {"revision": 3, **SEED, "authenticated": True}))
        self.assertEqual(self.send("GET", "/assets/new.png")[0], 404)
        self.assertEqual(self.send("GET", "/assets/logos/DNS.svg")[2], SVG)

    def test_invalid_restore_has_no_partial_effect(self):
        cookie = self.login().split(";", 1)[0]
        modified = deepcopy(SEED)
        modified["settings"]["title"] = "Darf nicht geschrieben werden"
        for name, data in {
            "assets/../new.png": PNG,
            "assets/doc.html": b"<html>Beispiel</html>",
            "assets/not-a-png.png": b"invalid",
            "assets/active.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
        }.items():
            with self.subTest(name=name):
                backup = self.archive(modified, {"assets/valid.png": PNG, name: data})
                self.assertEqual(self.send(
                    "POST", "/api/restore", backup, cookie=cookie,
                    headers={**MUTATION, "Content-Type": "application/zip"},
                )[0], 400)
                self.assertIsNone(self.store.asset("valid.png"))
        self.assertEqual(self.store.dashboard(), {"revision": 1, **SEED})
        self.assertEqual(self.store.asset("logos/DNS.svg"), SVG)

    def test_database_write_error_rolls_back_restore(self):
        with closing(self.store.connection()) as database, database:
            database.execute("""
                CREATE TRIGGER fail_asset_insert BEFORE INSERT ON assets
                BEGIN SELECT RAISE(ABORT, 'test failure'); END
            """)
        modified = deepcopy(SEED)
        modified["settings"]["title"] = "Sicherung"
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save(modified, assets={"new.png": PNG})
        self.assertEqual(self.store.dashboard(), {"revision": 1, **SEED})
        self.assertEqual(self.store.asset("logos/DNS.svg"), SVG)

    def test_polling_is_shared_persisted_and_invalidated(self):
        self.assertEqual(self.send("GET", "/api/status")[2]["items"]["dns"]["state"], "unknown")
        self.poller.start()
        self.assertTrue(self.provider.updated.wait(2))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = self.send("GET", "/api/status")[2]["items"]["dns"]
            if status["state"] == "ok":
                break
            time.sleep(0.02)
        self.assertEqual(status["state"], "ok")
        self.assertGreater(status["last_success"], 0)
        self.assertEqual(status["progress"], 20.5)
        self.assertEqual(self.provider.visits, ["dns"])
        self.poller.close()
        reopened = Store(self.data_dir, self.seed_dir)
        poller = StatusPoller(reopened, self.provider)
        self.addCleanup(poller.close)
        self.assertEqual(poller.snapshot()["items"]["dns"], status)
        self.assertEqual(poller.snapshot(status["checked_at"] + 181)["items"]["dns"]["state"], "stale")
        data = deepcopy(SEED)
        item = data["groups"][0]["items"][0]
        previous_signature = fingerprint(item)
        item["source"] = "different-source"
        self.store.save(data, expected_revision=1)
        self.store.write_status("dns", previous_signature, {**status, "state": "ok"}, int(time.time()), 180)
        replacement = self.poller.snapshot()["items"]["dns"]
        self.assertEqual((replacement["state"], replacement["last_success"]), ("unknown", None))

    def test_snapshot_interval_and_poller_freshness_are_independent(self):
        item = SEED["groups"][0]["items"][0]
        reading = {"label": "Host-Messwerte", "state": "ok", "metrics": [],
                   "observed_at": 10000, "measurement_max_age": 2100}
        def record(checked_at):
            result = clean_status(reading, checked_at)
            self.store.write_status(item["id"], fingerprint(item), result, checked_at, 180)
        record(11200)
        current = self.poller.snapshot(11260)["items"][item["id"]]
        self.assertEqual((current["state"], current["last_success"]), ("ok", 10000))
        self.assertEqual(self.poller.snapshot(11381)["items"][item["id"]]["state"], "stale")
        record(12100)
        self.assertEqual(self.poller.snapshot(12101)["items"][item["id"]]["state"], "stale")

    def test_optional_link_check_round_trip_and_invalidation(self):
        cookie = self.login().split(";", 1)[0]
        doc = self.store.dashboard()
        link = doc["groups"][0]["items"][1]

        def save():
            return self.send("PUT", "/api/dashboard", doc, headers=MUTATION, cookie=cookie)

        link["check_link"] = True
        self.assertEqual(save()[0], 400)  # Relative help link
        link["url"] = "https://example.org/page"
        link["check_link"] = "true"
        self.assertEqual(save()[0], 400)
        link["check_link"] = True
        code, _, saved = save()
        self.assertEqual(code, 200)
        self.assertTrue(saved["groups"][0]["items"][1]["check_link"])
        self.assertIn("help", self.poller.snapshot()["items"])
        with ZipFile(BytesIO(self.store.backup())) as archive:
            self.assertEqual(json.loads(archive.read("dashboard.json"))["dashboard"]["groups"], doc["groups"])

        original = fingerprint(link)
        ok = {"label": "Webseite erreichbar", "state": "ok", "metrics": []}
        self.store.write_status("help", original, ok, 1000, 180)
        doc["revision"] = saved["revision"]
        link["url"] = "https://example.org/new"
        code, _, saved = save()
        self.assertEqual(code, 200)
        self.store.write_status("help", original, ok, 1001, 180)
        self.assertIsNone(self.poller.snapshot()["items"]["help"]["last_success"])
        doc["revision"] = saved["revision"]
        link["check_link"] = False
        self.assertEqual(save()[0], 200)
        self.assertNotIn("help", self.poller.snapshot()["items"])
        self.assertNotIn("check_link", self.store.dashboard()["groups"][0]["items"][1])

    def test_link_failures_survive_restart_and_recover(self):
        doc = deepcopy(SEED)
        item = doc["groups"][0]["items"][1]
        item.update(url="https://example.org/", check_link=True)
        self.store.save(doc)
        signature = fingerprint(item)

        def record(state, when):
            self.store.write_status("help", signature, {"state": state, "label": "Webseite", "metrics": []}, when, 180)
            result = self.poller.snapshot(when)["items"]["help"]
            return result["state"], result["failures"], result["last_success"]

        self.assertEqual(record("down", 1000), ("unknown", 1, None))
        self.assertEqual(record("unknown", 1300), ("unknown", 0, None))
        self.assertEqual(record("down", 1600), ("unknown", 1, None))
        self.assertEqual(record("down", 1900), ("down", 2, None))
        self.assertEqual(record("ok", 2200), ("ok", 0, 2200))
        self.assertEqual(record("down", 2500), ("ok", 1, 2200))
        self.store = Store(self.data_dir, self.seed_dir)
        with patch("server.time.time", return_value=2560), patch("server.time.monotonic", return_value=7):
            self.poller = StatusPoller(self.store, self.provider)
        self.addCleanup(self.poller.close)
        self.assertEqual(self.poller.next_due[("help", signature)], 247)
        self.assertEqual(self.poller.snapshot(2750)["items"]["help"]["state"], "ok")
        self.assertEqual(record("down", 2800), ("down", 2, 2200))
        self.assertEqual(self.poller.snapshot(2910)["items"]["help"]["label"], "Vom Server nicht erreichbar")
        self.assertEqual(record("ok", 3100), ("ok", 0, 3100))
        self.assertEqual(self.poller.snapshot(3800)["items"]["help"]["state"], "stale")

    def test_link_poll_interval_and_timeout_count_once(self):
        doc = deepcopy(SEED)
        item = {"id": "link", "kind": "link", "name": "Link", "url": "https://example.org/", "check_link": True}
        doc["groups"][0]["items"] = [item]
        self.store.save(doc)
        first, second = Future(), Future()
        with patch.object(self.poller.pool, "submit", side_effect=[first, second]) as submit:
            def tick(t):
                with patch("server.time.monotonic", return_value=t), patch("server.time.time", return_value=5000+t):
                    self.poller.tick()

            tick(0)
            tick(10)
            self.assertEqual(self.poller.snapshot(5010)["items"]["link"]["failures"], 1)
            first.set_result((11, {"state": "ok", "label": "Verspätet", "metrics": []}))
            tick(11)
            tick(309)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(self.poller.snapshot(5309)["items"]["link"]["failures"], 1)
            tick(310)
            self.assertEqual(submit.call_count, 2)
            second.set_result((321, {"state": "ok", "label": "Verspätet", "metrics": []}))
            tick(321)
            self.assertEqual(self.poller.snapshot(5321)["items"]["link"]["state"], "down")

    def test_nas_measurement_survives_loss_of_monitoring_source(self):
        data = deepcopy(SEED)
        item = data["groups"][0]["items"][0]
        item.update(kind="nas", source="nas-volume")
        self.store.save(data, expected_revision=1)
        observed = 10000
        reading = {"state": "ok", "label": "Volume", "metrics": [{"label": "Frei", "value": "2 TB"}], "progress": 60}
        self.store.write_status(item["id"], fingerprint(item), reading, observed, 180)
        for offset in (60, 240):
            failure = {"state": "stale", "label": "Messquelle nicht erreichbar", "metrics": []}
            self.store.write_status(item["id"], fingerprint(item), failure, observed + offset, 180)
            value = self.poller.snapshot(observed + offset)["items"][item["id"]]
            self.assertEqual((value["state"], value["last_success"], value["progress"]), ("stale", observed, 60))
            self.assertEqual(value["metrics"], reading["metrics"])

    def test_infrastructure_status_preserves_dashboard(self):
        self.poller.infrastructure = "demo-host"
        self.assertEqual(self.send("GET", "/api/status")[2]["infrastructure"]["demo-host"]["state"], "unknown")
        self.poller.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = self.send("GET", "/api/status")[2]
            if status["infrastructure"]["demo-host"]["state"] == "ok":
                break
            time.sleep(0.02)
        self.assertEqual(status["infrastructure"]["demo-host"]["state"], "ok")
        self.assertEqual(status["items"].keys(), {"dns"})
        self.assertCountEqual(self.provider.visits, ["dns", "infra-demo-host"])
        self.assertEqual(self.store.dashboard(), {"revision": 1, **SEED})
        self.store.save(deepcopy(SEED), expected_revision=1)
        self.assertEqual(self.poller.snapshot()["infrastructure"]["demo-host"]["state"], "ok")

    def test_disposable_service_outage_and_status_expiration(self):
        class HealthyService(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = b'{"health":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        service = HTTPServer(("127.0.0.1", 0), HealthyService)
        origin = f"http://127.0.0.1:{service.server_port}"
        thread = threading.Thread(target=service.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def close_service():
            service.shutdown()
            service.server_close()
            thread.join(2)

        self.addCleanup(close_service)
        config = self.root / "integrations.json"
        config.write_text(json.dumps({
            "allowed_endpoints": [origin],
            "sources": [{"id": "dns", "kind": "http", "label": "Beispieldienst",
                         "url": origin + "/health", "required_json_keys": ["health"]}],
        }))
        document = deepcopy(SEED)
        item = document["groups"][0]["items"][0]
        item.update(kind="http", source="dns", url=origin)
        self.store.save(document, expected_revision=1)
        self.poller.provider = integrations
        self.poller.interval = 0.05
        self.poller.stale_after = 2

        def wait_for(state):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = self.send("GET", "/api/status")[2]["items"]["dns"]
                if result["state"] == state:
                    return result
                time.sleep(0.02)
            self.fail(f"Expected {state}; got {result!r}")

        with patch.dict(os.environ, {"INTEGRATIONS_FILE": str(config)}):
            self.poller.start()
            healthy = wait_for("ok")
            close_service()
            unavailable = wait_for("down")
            self.poller.close()

        self.assertEqual(healthy["last_success"], unavailable["last_success"])
        expired = self.poller.snapshot(unavailable["checked_at"] + 3)["items"]["dns"]
        self.assertEqual(expired["state"], "stale")
        self.assertEqual(expired["last_success"], healthy["last_success"])

    @unittest.skipUnless(shutil.which("openssl"), "openssl is needed to create a temporary TLS test certificate")
    def test_tls_keeps_http_read_only_and_allows_secure_editing(self):
        cert, key = self.root / "cert.pem", self.root / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        shared = Sessions(b"synthetic-test-password", secure=True)
        tls = tls_context(cert, key)
        self.assertEqual(tls.minimum_version, ssl.TLSVersion.TLSv1_2)
        admin = self.listener(sessions=shared, tls=tls)
        url = f"https://127.0.0.1:{admin.server_port}/?edit=1"
        public = self.listener(sessions=shared, read_only=True, edit_url=url)
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.load_verify_locations(cafile=cert)
        cookie_header = self.login(
            listener=admin, context=client_context,
            headers={**MUTATION, "Origin": f"https://127.0.0.1:{admin.server_port}"},
        )
        self.assertIn("Secure", cookie_header)
        cookie = cookie_header.split(";", 1)[0]
        dashboard = self.send("GET", "/api/dashboard", listener=public, cookie=cookie)[2]
        self.assertEqual((dashboard["authenticated"], dashboard["edit_url"]), (False, url))
        self.assertEqual(self.send("GET", "/api/backup", listener=public, cookie=cookie)[0], 403)
        self.assertEqual(self.send(
            "POST", "/api/login", {"password": "synthetic-test-password"}, listener=public, headers=MUTATION,
        )[0], 403)
        data = {"revision": 1, **deepcopy(SEED)}
        data["settings"]["title"] = "Über TLS aktualisiert"
        self.assertEqual(self.send("PUT", "/api/dashboard", data, listener=public, cookie=cookie, headers=MUTATION)[0], 403)
        self.assertEqual(self.send(
            "PUT", "/api/dashboard", data, listener=admin, context=client_context, cookie=cookie, headers=MUTATION,
        )[0], 200)
        self.assertEqual(self.send("GET", "/api/dashboard", listener=public)[2]["settings"], data["settings"])

    def archive(self, document, assets):
        with BytesIO() as output:
            with ZipFile(output, "w") as archive:
                archive.writestr("dashboard.json", json.dumps({
                    "schema_version": 1, "dashboard": {"revision": 1, **document},
                }))
                for name, data in assets.items():
                    archive.writestr(name, data)
            return output.getvalue()


if __name__ == "__main__":
    unittest.main()
