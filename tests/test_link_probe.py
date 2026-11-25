import io
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch

import integrations


class HeadersOnly(io.BytesIO):
    def read(self, *_):
        raise AssertionError("Link checks must never read the response body")


class LinkProbeTests(unittest.TestCase):
    def mock(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def setUp(self):
        self.configure = self.mock("integrations._configuration")
        self.configure.return_value = {}, set()
        self.resolve = self.mock("integrations.socket.getaddrinfo")
        self.resolve.return_value = [self.address("93.184.216.34", 443)]
        self.socket_factory = self.mock("integrations.socket.socket")
        self.sock = self.socket_factory.return_value
        self.sock.__enter__.return_value = self.sock
        self.tls = self.mock("integrations.ssl.create_default_context").return_value
        self.secure = self.tls.wrap_socket.return_value
        self.response(self.secure, 200)

    @staticmethod
    def address(ip, port):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))

    @staticmethod
    def response(sock, code, location=None):
        header = b"" if location is None else b"Location: " + location.encode() + b"\r\n"
        sock.makefile.return_value = HeadersOnly(
            f"HTTP/1.1 {code} Status\r\n".encode() + header + b"Content-Length: 10\r\n\r\n0123456789"
        )

    def probe(self, url="https://example.com/", **kwargs):
        return integrations.probe({"kind": "link", "check_link": True, "url": url, **kwargs})

    def test_connects_only_to_validated_ip_and_preserves_http_and_tls_host(self):
        url = "https://bücher.example/film/über?suche=schön#privat"
        result = self.probe(url)
        self.assertEqual(result["state"], "ok")
        self.resolve.assert_called_once_with("xn--bcher-kva.example", 443,
                                             type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        self.sock.connect.assert_called_once_with(("93.184.216.34", 443))
        self.tls.wrap_socket.assert_called_once_with(self.sock, server_hostname="xn--bcher-kva.example")
        packet = self.secure.sendall.call_args.args[0]
        self.assertIn(b"GET /film/%C3%BCber?suche=sch%C3%B6n HTTP/1.1\r\n", packet)
        self.assertIn(b"Host: xn--bcher-kva.example\r\n", packet)
        self.assertIn(b"User-Agent: LandingpageKit/", packet)
        self.assertNotIn(b"privat", packet)
        self.assertNotIn("suche", str(result))
        self.secure.close.assert_called_once()

    def test_redirects_and_http_failures(self):
        for code, location, expected in (
            (204, None, "ok"), (299, None, "ok"), (300, "/next", "ok"),
            (301, "http://localhost/admin", "ok"), (302, "/next", "ok"), (303, "/next", "ok"),
            (307, "/next", "ok"), (308, "/next", "ok"), (302, None, "down"),
            (304, "/next", "down"), (305, "/next", "down"), (401, None, "down"),
            (403, None, "down"), (404, None, "down"), (503, None, "down"),
        ):
            with self.subTest(code=code, location=location):
                self.response(self.secure, code, location)
                self.secure.sendall.reset_mock()
                self.resolve.reset_mock()
                result = self.probe()
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["metrics"], [{"label": "Antwort", "value": f"HTTP {code}"}])
                self.resolve.assert_called_once()
                self.secure.sendall.assert_called_once()

    def test_disabled_and_invalid_targets_never_resolve(self):
        for url in ("file:///etc/passwd", "/only-local", "https://user:pw@site.example",
                    "https://example.com:8443/", "https://example.com:0/",
                    "https://example.com\\@other/", "https://[::1]suffix/",
                    "https://example.com//example.org", "https://example.com/\nheader"):
            with self.subTest(url=url):
                self.assertEqual(self.probe(url)["state"], "unknown")
        self.assertEqual(self.probe(check_link=False)["state"], "unknown")
        self.resolve.assert_not_called()
        self.socket_factory.assert_not_called()

    def test_nonpublic_and_mixed_dns_answers_are_rejected_before_connecting(self):
        for ip in ("127.0.0.1", "169.254.169.254", "10.1.2.3", "100.64.0.1",
                   "192.0.2.1", "224.0.0.1", "::1", "fc00::1", "fe80::1", "ff02::1",
                   "::ffff:127.0.0.1", "64:ff9b::a9fe:a9fe", "2002:7f00:1::"):
            with self.subTest(ip=ip):
                self.resolve.return_value = [self.address("93.184.216.34", 443), self.address(ip, 443)]
                self.assertEqual(self.probe()["state"], "unknown")
                self.socket_factory.assert_not_called()

    def test_explicit_private_origin_and_port(self):
        self.configure.return_value = {}, {("http", "local.example", 8081)}
        self.resolve.return_value = [self.address("10.0.0.5", 8081)]
        self.response(self.sock, 200)
        self.assertEqual(self.probe("http://local.example:8081/")["state"], "ok")
        self.assertIn(b"Host: local.example:8081\r\n", self.sock.sendall.call_args.args[0])
        self.sock.connect.assert_called_once_with(("10.0.0.5", 8081))

    def test_dns_timeout_and_tls_errors_count_as_outages(self):
        self.resolve.side_effect = socket.gaierror()
        self.assertEqual(self.probe()["state"], "down")
        self.resolve.side_effect = None
        self.sock.connect.side_effect = TimeoutError()
        self.assertEqual(self.probe()["state"], "down")
        self.sock.close.assert_called_once()
        self.sock.connect.side_effect = None
        self.tls.wrap_socket.side_effect = ssl.SSLError()
        self.assertEqual(self.probe()["state"], "down")

    def test_alternate_validated_address_works_when_first_is_unreachable(self):
        self.resolve.return_value = [self.address("2606:4700:4700::1111", 443),
                                     self.address("1.1.1.1", 443)]
        first = MagicMock()
        first.connect.side_effect = OSError()
        self.socket_factory.side_effect = [first, self.sock]
        self.assertEqual(self.probe()["state"], "ok")
        first.close.assert_called_once()
        self.sock.connect.assert_called_once_with(("1.1.1.1", 443))
