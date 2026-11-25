import contextlib
import io
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import unittest
from unittest.mock import patch

import server
from tools import configure


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def example_document(name="dashboard.example.yaml"):
    with (EXAMPLES / name).open("rb") as file:
        return configure.read_dashboard(file)


class SetupTests(unittest.TestCase):
    def run_board_command(self, command, content=None):
        if content is None:
            content = (EXAMPLES / "dashboard.example.yaml").read_bytes()
        output = io.StringIO()
        with patch("sys.stdin", io.TextIOWrapper(io.BytesIO(content))), contextlib.redirect_stdout(output):
            configure.run(command)
        return output.getvalue()

    def test_example_files_are_valid(self):
        self.assertEqual(len(example_document()["groups"]), 2)
        self.assertEqual(len(example_document("dashboard-monitoring.example.yaml")["groups"][0]["items"]), 2)
        with (EXAMPLES / "integrations.example.yaml").open("rb") as file:
            self.assertEqual([s["id"] for s in configure.source_document(file)["sources"]], ["dashboard-health", "disk-usage"])

    def test_english_cli_and_example_files(self):
        with patch.dict(os.environ, {"DASHBOARD_LANGUAGE": "en"}):
            for name in ("dashboard.example.yaml", "dashboard-monitoring.example.yaml"):
                yaml_file = EXAMPLES / "en" / name
                self.assertEqual(self.run_board_command(["check"], yaml_file.read_bytes()), "Dashboard YAML is valid.\n")
            sources = (EXAMPLES / "en" / "integrations.example.yaml").read_bytes()
            self.assertEqual(self.run_board_command(["check-sources"], sources), "Data source YAML is valid.\n")
            with self.assertRaisesRegex(ValueError, "Invalid YAML. Line 2, column 1"):
                self.run_board_command(["check"], b"title: duplicate\ntitle: value\n")

    def test_invalid_yaml_reports_location_without_input_values(self):
        for content in (b"title: private\ntitle: private2\n", b"item: &private [*private]\n"):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "Ungültiges YAML. Zeile") as error:
                configure.read_yaml(io.BytesIO(content))
            self.assertNotIn("private", str(error.exception))

    def test_targets_need_explicit_permission(self):
        content = b"""allowed_endpoints: []
sources:
  - id: test
    kind: http
    label: Test
    url: http://example.org/healthz
"""
        with self.assertRaisesRegex(ValueError, "allowed_endpoints"):
            configure.source_document(io.BytesIO(content))

    def test_init_only_writes_seed_for_an_empty_database(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"DATA_DIR": temp, "SEED_DIR": f"{temp}/seed"}):
            self.run_board_command(["init"])
            store = server.Store(Path(temp), Path(temp, "seed"))
            document = store.dashboard()
            self.assertEqual(document, {"revision": 1, **example_document()})
            with self.assertRaisesRegex(ValueError, "SQLite ist bereits vorhanden"):
                self.run_board_command(["init"], (EXAMPLES / "dashboard-monitoring.example.yaml").read_bytes())
            self.assertEqual(store.dashboard(), document)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for local TLS")
    def test_generated_tls_is_trusted_for_the_loopback_address(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp, "tls.pem")
            configure.generate_local_tls(bundle)
            self.assertEqual(bundle.stat().st_mode & 0o777, 0o600)
            output = io.TextIOWrapper(io.BytesIO())
            with patch("sys.stdout", output):
                configure.export_ca(bundle)
            authority = output.buffer.getvalue()
            self.assertNotIn(b"PRIVATE KEY", authority)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(bundle, bundle)
            client_context = ssl.create_default_context(cadata=authority.decode("ascii"))
            for name in ("localhost", "127.0.0.1", "::1"):
                with self.subTest(hostname=name):
                    self.assert_handshake(server_context, client_context, name)

    def assert_handshake(self, server_context, client_context, hostname):
        server_input, server_output = ssl.MemoryBIO(), ssl.MemoryBIO()
        client_input, client_output = ssl.MemoryBIO(), ssl.MemoryBIO()
        server_endpoint = server_context.wrap_bio(server_input, server_output, server_side=True)
        client_endpoint = client_context.wrap_bio(client_input, client_output, server_hostname=hostname)
        for _ in range(16):
            complete = 0
            for endpoint in (server_endpoint, client_endpoint):
                try:
                    endpoint.do_handshake()
                    complete += 1
                except ssl.SSLWantReadError:
                    pass
            if complete == 2:
                return
            client_input.write(server_output.read())
            server_input.write(client_output.read())
        self.fail("TLS handshake did not complete")

    @patch("tools.configure.Api")
    def test_apply_requires_current_revision(self, api_class):
        api = api_class.return_value
        api.request.return_value = {"revision": 4, **example_document("dashboard-monitoring.example.yaml")}
        self.assertIn("Aktuelle Revision: 4", self.run_board_command(["preview"]))
        with self.assertRaisesRegex(ValueError, "Revision hat sich geändert"):
            self.run_board_command(["apply", "--revision", "3"])
        api.login.assert_not_called()
        self.run_board_command(["apply", "--revision", "4"])
        api.login.assert_called_once()
        api.request.assert_called_with("/api/dashboard", method="PUT", payload={"revision": 4, **example_document()})


if __name__ == "__main__":
    unittest.main()
