import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import integrations
from tools import collect_checkmk, collect_ssh_storage


class ServiceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        status, headers, body = self.server.responses.get(self.path.split("?")[0], (404, {}, {}))
        self.send_response(status)
        for header, value in headers.items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *_):
        pass


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.file = self.directory / "config.json"
        environment = patch.dict(os.environ, {"INTEGRATIONS_FILE": str(self.file),
                                              "INTEGRATIONS_METRICS_DIR": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)

    def server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ServiceHandler)
        server.responses = {}
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_port}"

    def configure(self, *entries, allowed=()):
        self.file.write_text(json.dumps({"allowed_endpoints": list(allowed), "sources": list(entries)}))

    def test_unknown_configuration(self):
        self.assertEqual(integrations.sources(), [])
        self.assertEqual(integrations.probe({"id": "missing"})["state"], "unknown")

    def test_pihole_uses_actual_counters_and_projects_safe_metadata(self):
        server, origin = self.server()
        server.responses["/api/stats/summary"] = (200, {}, {
            "queries": {"total": 12345, "blocked": 1000, "percent_blocked": 8.1},
            "clients": {"active": 14}, "gravity": {"domains_being_blocked": 1234567},
        })
        self.configure({"id": "pihole", "label": "Pi-hole", "kind": "pihole", "url": origin + "/api/stats/summary"},
                       allowed=(origin,))
        self.assertEqual(integrations.sources(), [{"id": "pihole", "label": "Pi-hole", "kind": "pihole"}])
        result = integrations.probe({"id": "pihole", "url": "https://example.invalid", "kind": "tcp"})
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["metrics"][0], {"label": "Anfragen (24 h)", "value": "12.345"})
        self.assertEqual(result["metrics"][1]["value"], "1.000 · 8,1 %")
        self.assertEqual(server.requests, ["/api/stats/summary"])

    def test_pihole_empty_day_and_malformed_or_unauthorized_response(self):
        server, origin = self.server()
        self.configure({"id": "pi", "label": "Pi", "kind": "pihole", "url": origin + "/stats"}, allowed=(origin,))
        server.responses["/stats"] = (200, {}, {
            "queries": {"total": 0, "blocked": 0, "percent_blocked": 0},
            "clients": {"active": 0}, "gravity": {"domains_being_blocked": 175},
        })
        self.assertEqual(integrations.probe({"id": "pi"})["state"], "ok")
        for status, body in ((200, {"queries": {}}), (401, {"error": "unavailable"})):
            server.responses["/stats"] = (status, {}, body)
            self.assertEqual(integrations.probe({"id": "pi"})["state"], "unknown")

    def test_never_follows_redirect_or_calls_unapproved_origin(self):
        server, origin = self.server()
        other, other_origin = self.server()
        server.responses["/redirect"] = (302, {"Location": other_origin + "/status"}, {})
        self.configure({"id": "redirect", "label": "Redirect", "kind": "http", "url": origin + "/redirect"},
                       {"id": "denied", "label": "Denial", "kind": "http", "url": other_origin + "/status"},
                       {"id": "accepted", "label": "Login", "kind": "http", "url": origin + "/redirect", "statuses": [302]},
                       allowed=(origin,))
        self.assertEqual(integrations.probe({"id": "redirect"})["state"], "down")
        self.assertEqual(integrations.probe({"id": "denied"})["state"], "unknown")
        self.assertEqual(integrations.probe({"id": "accepted"})["state"], "ok")
        self.assertEqual(other.requests, [])

    def test_application_health_fields_change_service_status(self):
        server, origin = self.server()
        self.configure({"id": "demo-app", "label": "Beispieldienst", "kind": "http", "url": origin + "/api/health",
                        "expect_json": {"database": "ok"}}, allowed=(origin,))
        for answer, expected in (("ok", "ok"), ("unhealthy", "warning")):
            server.responses["/api/health"] = (200, {}, {"database": answer})
            self.assertEqual(integrations.probe({"id": "demo-app"})["state"], expected)

    def test_poll_source_shared_between_tiles_and_allowlisted_custom_http(self):
        server, origin = self.server()
        server.responses["/api/status"] = (200, {}, {"Version": "1.2.3", "secret": "never published"})
        self.configure({"id": "sample-service", "label": "Sample service", "kind": "http", "url": origin + "/api/status",
                        "metric_fields": {"Version": "Version"}}, allowed=(origin,))
        results = [integrations.probe({"id": item, "source": "sample-service"}) for item in ("custom-id", "second-id")]
        self.assertTrue(all(result["metrics"] == [{"label": "Version", "value": "1.2.3"}] for result in results))
        custom = {"id": "custom", "kind": "http", "url": origin + "/api/status"}
        self.assertEqual(integrations.probe(custom)["state"], "ok")
        self.assertEqual(integrations.probe({**custom, "url": "http://example.invalid"})["state"], "unknown")

    def test_pihole_disabled_blocking_is_reported_separately(self):
        server, origin = self.server()
        self.configure({"id": "pi", "label": "Pi", "kind": "pihole", "url": origin + "/stats",
                        "blocking_url": origin + "/blocking"}, allowed=(origin,))
        server.responses["/stats"] = (200, {}, {
            "queries": {"total": 6, "blocked": 0, "percent_blocked": 0},
            "clients": {"active": 1}, "gravity": {"domains_being_blocked": 175},
        })
        server.responses["/blocking"] = (200, {}, {"blocking": "disabled"})
        probe = integrations.probe({"id": "pi"})
        self.assertEqual(probe["state"], "warning")
        self.assertEqual(probe["metrics"][-1], {"label": "DNS-Blockierung", "value": "Ausgeschaltet"})

    def test_pihole_distinguishes_failed_dns_and_unavailable_api(self):
        server, origin = self.server()
        self.configure({"id": "pi", "label": "Pi", "kind": "pihole", "url": origin + "/stats",
                        "dns_file": "pi-dns.json"}, allowed=(origin,))
        server.responses["/stats"] = (200, {}, {
            "queries": {"total": 6, "blocked": 0, "percent_blocked": 0},
            "clients": {"active": 1}, "gravity": {"domains_being_blocked": 175},
        })
        now = time.time()
        dns = collect_checkmk.dns_snapshot([["pi", 2, 1, now - 2, ""]], "pi", now)
        collect_checkmk.write_snapshot(self.directory, "pi-dns.json", dns)
        result = integrations.probe({"id": "pi"})
        self.assertEqual(result["state"], "down")
        self.assertEqual(result["metrics"][-1], {"label": "DNS-Auflösung", "value": "Fehlgeschlagen"})
        dns = collect_checkmk.dns_snapshot([["pi", 0, 1, now - 2, "time=0.025s;;;0.000"]], "pi", now)
        collect_checkmk.write_snapshot(self.directory, "pi-dns.json", dns)
        server.responses["/stats"] = (503, {}, {})
        result = integrations.probe({"id": "pi"})
        self.assertEqual(result["state"], "warning")
        self.assertEqual(result["metrics"], [
            {"label": "DNS-Auflösung", "value": "Antwortet · 25 ms"},
            {"label": "Statistik-API", "value": "Nicht verfügbar"},
        ])

    def test_pin_rejects_wrong_peer_before_http_request(self):
        self.configure({"id": "pihole", "label": "Pi-hole", "kind": "http", "url": "https://example.invalid",
                        "tls_sha256": "0" * 64}, allowed=("https://example.invalid",))
        with patch.object(integrations.http.client, "HTTPSConnection") as connection:
            connection.return_value.sock.getpeercert.return_value = b"other peer"
            self.assertEqual(integrations.probe({"id": "pihole"})["state"], "unknown")
            connection.return_value.request.assert_not_called()
            connection.return_value.close.assert_called_once()

    def test_tcp_check_stops_when_port_closes(self):
        server, _ = self.server()
        target = f"tcp://127.0.0.1:{server.server_port}"
        self.configure({"id": "demo-host", "label": "Beispielhost", "kind": "tcp", "url": target}, allowed=(target,))
        self.assertEqual(integrations.probe({"id": "demo-host"})["state"], "ok")
        server.shutdown()
        server.server_close()
        self.assertEqual(integrations.probe({"id": "demo-host"})["state"], "down")

    def test_host_warning_when_a_port_is_refused_and_down_when_neither_responds(self):
        server, _ = self.server()
        first = "tcp://127.0.0.1:12345"
        second = f"tcp://127.0.0.1:{server.server_port}"
        self.configure({"id": "demo-host", "label": "Beispielhost", "kind": "host", "targets": [
            {"label": "Dienst A", "url": first}, {"label": "Dienst B", "url": second},
        ]}, allowed=(first, second))
        item = {"id": "infra-demo-host", "source": "demo-host"}
        with patch.object(integrations.socket, "create_connection") as connect:
            for outcomes, expected in (
                ([ConnectionRefusedError(), unittest.mock.MagicMock()], "warning"),
                ([ConnectionRefusedError(), TimeoutError()], "warning"),
                ([TimeoutError(), TimeoutError()], "down"),
            ):
                connect.side_effect = outcomes
                result = integrations.probe(item)
                self.assertEqual(result["state"], expected)
                self.assertEqual(len(result["metrics"]), 2)

    def test_prometheus_reports_failing_target_while_server_is_ready(self):
        server, origin = self.server()
        self.configure({"id": "prometheus", "label": "Prometheus", "kind": "prometheus", "url": origin + "/"},
                       allowed=(origin,))
        server.responses["/-/ready"] = (200, {}, {})
        server.responses["/api/v1/targets"] = (200, {}, {"status": "success", "data": {
            "activeTargets": [{"health": "up"}, {"health": "down"}],
        }})
        probe = integrations.probe({"id": "prometheus"})
        self.assertEqual(probe["state"], "warning")
        self.assertEqual(probe["metrics"][0]["value"], "1 / 2")
        self.assertEqual(server.requests, ["/-/ready", "/api/v1/targets?state=active"])

    def test_snmp_volume_uses_underlying_sample_timestamps_and_storage_units(self):
        server, origin = self.server()
        self.configure({"id": "snmp-volume", "label": "Volume 1", "kind": "snmp_volume", "url": origin,
                        "job": "snmp", "instance": "example", "volume": "/srv/storage",
                        "health_metric": "example:volume_healthy"}, allowed=(origin,))
        values = {"hrStorageSize": 1500000000, "hrStorageUsed": 1125000000, "hrStorageAllocationUnits": 8192,
                  "up": 1, "example:volume_healthy": 1}
        observed = time.time() - 30
        sample_rows = [{"metric": {"source_metric": key}, "value": [observed, str(observed)]} for key in values]
        value_rows = [
            {"metric": {"source_value" if ":" in key else "__name__": key}, "value": [observed, str(value)]}
            for key, value in values.items()
        ]
        server.responses["/api/v1/query"] = (200, {}, {"status": "success", "data": {
            "resultType": "vector", "result": [*sample_rows, *value_rows],
        }})
        probe = integrations.probe({"id": "snmp-volume"})
        self.assertEqual(probe["state"], "ok")
        self.assertEqual(probe["progress"], 75.0)
        self.assertEqual(probe["observed_at"], observed)
        self.assertEqual(probe["metrics"][2]["value"], "12,29 TB")
        self.assertIn('{__name__=~"hrStorageSize|hrStorageUsed|hrStorageAllocationUnits",',
                      parse_qs(urlsplit(server.requests[0]).query)["query"][0])
        self.assertIn('label_replace(example:volume_healthy,"source_value"',
                      parse_qs(urlsplit(server.requests[0]).query)["query"][0])
        value_rows[4]["value"][1] = "0"
        self.assertEqual(integrations.probe({"id": "snmp-volume"})["state"], "warning")
        value_rows[4]["value"][1] = "1"
        value_rows[1]["value"][1] = "1350000000"
        self.assertEqual(integrations.probe({"id": "snmp-volume"})["state"], "warning")
        sample_rows[0]["value"][1] = str(time.time() - 250)
        self.assertEqual(integrations.probe({"id": "snmp-volume"})["state"], "stale")
        value_rows[3]["value"][1] = "0"
        result = integrations.probe({"id": "snmp-volume"})
        self.assertEqual(result["state"], "stale")
        self.assertIn("SNMP", result["detail"])

    def test_prometheus_outage_leaves_nas_availability_unknown(self):
        server, origin = self.server()
        self.configure({"id": "snmp-volume", "label": "Volume 1", "kind": "snmp_volume", "url": origin,
                        "job": "snmp", "instance": "example", "volume": "/srv/storage"}, allowed=(origin,))
        server.responses["/api/v1/query"] = (503, {}, {})
        expected = "Speichermessung über Prometheus nicht verfügbar"
        item = {"source": "snmp-volume"}
        self.assertEqual(integrations.probe(item)["state"], "stale")
        self.assertEqual(integrations.probe(item)["detail"], expected)
        for error in (TimeoutError(), integrations.ssl.SSLError()):
            with patch.object(integrations, "_get", side_effect=error):
                probe = integrations.probe(item)
                self.assertEqual(probe["state"], "stale")
                self.assertEqual(probe["detail"], expected)

    def test_snapshot_retains_metrics_and_exposes_freshness(self):
        self.configure({"id": "nas-volume", "label": "Storage", "kind": "snapshot", "display_kind": "nas",
                        "file": "nas-volume.json", "max_age": 240})
        stamp = time.time()
        sample = collect_checkmk.measurement("ok", stamp, (("Kapazität", "1 TB"),), progress=20)
        collect_checkmk.write_snapshot(self.directory, "nas-volume.json", sample)
        self.assertEqual(integrations.sources()[0]["kind"], "nas")
        probe = integrations.probe({"id": "nas-volume"})
        self.assertEqual(probe["state"], "ok")
        self.assertEqual(probe["metrics"], [{"label": "Kapazität", "value": "1 TB"}])
        self.assertEqual(probe["progress"], 20)
        with patch.object(integrations.time, "time", return_value=stamp + 241):
            probe = integrations.probe({"id": "nas-volume"})
        self.assertEqual(probe["state"], "stale")
        self.assertEqual(probe["observed_at"], stamp)

    def test_ssh_storage_snapshot_reports_actual_capacity_and_available_space(self):
        self.configure({"id": "nas-volume", "label": "Storage", "kind": "snapshot", "display_kind": "nas",
                        "file": "nas-volume.json", "max_age": 240})
        config = self.ssh_storage_config()
        row = "/dev/example 2000000000 500000000 1400000000 25% /srv/storage"
        with patch.object(collect_ssh_storage.subprocess, "run", return_value=self.sample_df(row)) as ssh:
            snapshot = collect_ssh_storage.storage_snapshot(config)
        command, = ssh.call_args.args
        self.assertEqual(command[:4], ("/usr/bin/ssh", "-F", "/dev/null", "-T"))
        self.assertEqual(command[-7:], ("-p", "2222", "-l", "reader", "--", "nas.example.org", "df"))
        for option in ("UserKnownHostsFile=instance/config/nas_known_hosts", "GlobalKnownHostsFile=/dev/null",
                       "StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "BatchMode=yes", "ConnectTimeout=3"):
            self.assertIn(option, command)
        self.assertEqual(ssh.call_args.kwargs["timeout"], 5)
        collect_checkmk.write_snapshot(self.directory, "nas-volume.json", snapshot)
        probe = integrations.probe({"id": "nas-volume"})
        self.assertEqual(probe["state"], "ok")
        self.assertEqual(probe["progress"], 25.0)
        self.assertEqual(probe["metrics"], [
            {"label": "Belegt", "value": "0,51 TB"},
            {"label": "Frei", "value": "1,43 TB"},
            {"label": "Kapazität", "value": "2,05 TB"},
            {"label": "Auslastung", "value": "25,0 %"},
        ])

    def test_ssh_storage_warns_at_85_percent(self):
        config = self.ssh_storage_config()
        row = "/dev/example 100000 85000 14000 86% /srv/storage"
        with patch.object(collect_ssh_storage.subprocess, "run", return_value=self.sample_df(row)):
            snapshot = collect_ssh_storage.storage_snapshot(config)
        self.assertEqual(snapshot["state"], "warning")
        self.assertEqual(snapshot["progress"], 85.0)
        self.assertEqual(snapshot["metrics"][-1]["value"], "85,0 %")

    def test_ssh_storage_rejects_unmounted_or_invalid_results(self):
        config = self.ssh_storage_config()
        for row in (
            "/dev/root 100000 85000 14000 86% /",
            "/dev/example 0 0 0 0% /srv/storage",
            "/dev/example 100000 -1 14000 86% /srv/storage",
            "/dev/example 100000 85000 16000 86% /srv/storage",
            "/dev/example 100000 85000 14000 86% /srv/storage\n/dev/root 100 40 50 45% /",
        ):
            with self.subTest(row=row):
                with patch.object(collect_ssh_storage.subprocess, "run", return_value=self.sample_df(row)):
                    snapshot = collect_ssh_storage.storage_snapshot(config)
                self.assertEqual(snapshot["state"], "unknown")
                self.assertEqual(snapshot["metrics"], [])
                self.assertNotIn("progress", snapshot)

    def test_ssh_storage_clears_capacity_on_ssh_failure(self):
        config = self.ssh_storage_config()
        for error in (subprocess.TimeoutExpired("ssh", 5), subprocess.CalledProcessError(255, "ssh")):
            with self.subTest(error=error):
                with patch.object(collect_ssh_storage.subprocess, "run", side_effect=error):
                    snapshot = collect_ssh_storage.storage_snapshot(config)
                self.assertEqual(snapshot["state"], "unknown")
                self.assertIn("SSH", snapshot["detail"])
                self.assertEqual(snapshot["metrics"], [])
                self.assertNotIn("progress", snapshot)
        config.unlink()
        self.assertEqual(collect_ssh_storage.storage_snapshot(config)["state"], "unknown")

    def test_ssh_storage_rejects_config_values_that_change_ssh_behavior(self):
        for override in (
            {"host": "-oProxyCommand=anything"},
            {"host": "reader@nas.example.org"},
            {"user": "reader@nas.example.org"},
            {"known_hosts_file": "pinned_keys another_file"},
            {"known_hosts_file": "%d/.ssh/known_hosts"},
            {"mountpoint": "/srv/../storage"},
        ):
            with self.subTest(override=override):
                config = self.ssh_storage_config(**override)
                with patch.object(collect_ssh_storage.subprocess, "run") as ssh:
                    snapshot = collect_ssh_storage.storage_snapshot(config)
                ssh.assert_not_called()
                self.assertEqual(snapshot["state"], "unknown")
                self.assertIn("Speicherkonfiguration", snapshot["detail"])

    def ssh_storage_config(self, **override):
        path = self.directory / "nas-storage.json"
        path.write_text(json.dumps({
            "host": "nas.example.org", "port": 2222, "user": "reader",
            "identity_file": "instance/config/nas_key", "known_hosts_file": "instance/config/nas_known_hosts",
            "mountpoint": "/srv/storage", **override,
        }))
        return path

    def sample_df(self, row):
        return subprocess.CompletedProcess([], 0, f"Filesystem 1024-blocks Used Available Capacity Mounted on\n{row}\n".encode())


if __name__ == "__main__":
    unittest.main()
