"""Dashboard-YAML einrichten und die lokale Administration konfigurieren."""

import argparse
import difflib
from getpass import getpass
from http.client import HTTPException
import ipaddress
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.error import HTTPError, URLError

import yaml

# Allow the same entry point in the image and when working from a source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations import DISPLAY_KINDS, ProbeError, _endpoint
from server import MAX_DOCUMENT, RequestError, json_bytes, text_value, validate_dashboard
from tools.cli_language import cli_language, message
from tools.manage import Api, dashboard, pretty


class ConfigurationLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise yaml.YAMLError(message("YAML-Aliasse werden nicht unterstützt."), event.start_mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise yaml.YAMLError(message("Schlüssel müssen eindeutig und Text sein."), key_node.start_mark)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def read_yaml(stream):
    raw = stream.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise ValueError(message("Die YAML-Datei überschreitet 1 MiB."))
    try:
        value = yaml.load(raw, Loader=ConfigurationLoader)
        json_bytes(value)
        return value
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        if not mark and len(error.args) == 2:
            mark = error.args[1]
        position = message(" Zeile {line}, Spalte {column}.", line=mark.line + 1, column=mark.column + 1) if mark else ""
        raise ValueError(message("Ungültiges YAML.") + position) from None
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValueError(message("Die YAML-Daten müssen sich als JSON darstellen lassen.")) from None


def read_dashboard(stream):
    document = read_yaml(stream)
    if isinstance(document, dict) and document.keys() - {"settings", "groups"}:
        raise ValueError(message("Die Dashboard-Datei enthält ausschließlich settings und groups."))
    return validate_dashboard(document, require_revision=False)


def replace_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".config-", delete=False) as temp:
        pending = Path(temp.name)
        try:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


def source_document(stream):
    document = read_yaml(stream)
    if not isinstance(document, dict) or document.keys() != {"allowed_endpoints", "sources"}:
        raise ValueError(message("Erwartet werden allowed_endpoints und sources."))
    if not isinstance(document["allowed_endpoints"], list) or not isinstance(document["sources"], list):
        raise ValueError(message("allowed_endpoints und sources müssen Listen sein."))
    endpoints = {_endpoint(url, origin_only=True)[0] for url in document["allowed_endpoints"]}
    ids = set()
    for index, source in enumerate(document["sources"], 1):
        try:
            validate_source(source, endpoints)
            if source["id"] in ids:
                raise ValueError(message("Die ID ist bereits eingetragen."))
            ids.add(source["id"])
        except (ValueError, ProbeError, RequestError, KeyError, TypeError) as error:
            if isinstance(error, (RequestError, ProbeError)):
                reason = error.localized(cli_language())
            elif isinstance(error, ValueError):
                reason = str(error)
            else:
                reason = message("Ungültige Felder.")
            raise ValueError(message("Messquelle {index}. {reason}", index=index, reason=reason)) from None
    if len(json_bytes(document)) > MAX_DOCUMENT:
        raise ValueError(message("Die Messquellen sind zu groß."))
    return document


def validate_source(source, endpoints):
    if not isinstance(source, dict):
        raise ValueError(message("Eine Quelle muss als Zuordnung eingetragen werden."))
    source_id = source.get("id")
    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", source_id):
        raise ValueError(message("Die ID darf Kleinbuchstaben, Ziffern und Bindestriche enthalten."))
    text_value(source.get("label"), 160, "Quellenname")
    kind = source.get("kind")
    if not isinstance(kind, str) or kind not in DISPLAY_KINDS:
        raise ValueError(message("Unbekannte Art der Messquelle."))
    if "display_kind" in source and source["display_kind"] not in DISPLAY_KINDS.values():
        raise ValueError(message("Ungültiger Kacheltyp für die Messquelle."))

    if kind == "snapshot":
        validate_snapshot(source.get("file"), source.get("max_age", 180))
        return
    if kind == "host":
        targets = source.get("targets")
        if not isinstance(targets, list) or len(targets) != 2 or not all(isinstance(t, dict) for t in targets):
            raise ValueError(message("Eine Hostquelle benötigt zwei TCP-Ziele."))
        first, second = [validate_target(target["url"], endpoints, {"tcp"}) for target in targets]
        if first[1] != second[1] or first[2] == second[2]:
            raise ValueError(message("Die Ziele benötigen denselben Host und unterschiedliche Ports."))
        for target in targets:
            text_value(target.get("label"), 160, "Zielname")
        return

    schemes = {"tcp"} if kind == "tcp" else {"http", "https"}
    origin = validate_target(source.get("url"), endpoints, schemes, origin_only=kind in {"snmp_volume", "prometheus"})
    if "tls_sha256" in source and (origin[0] != "https" or not isinstance(source["tls_sha256"], str)
                                 or not re.fullmatch(r"[0-9a-fA-F]{64}", source["tls_sha256"])):
        raise ValueError(message("TLS-Fingerabdrücke benötigen HTTPS und 64 Hexadezimalzeichen."))
    if kind == "http":
        statuses = source.get("statuses", [200, 204])
        keys = source.get("required_json_keys", [])
        if not isinstance(statuses, list) or not statuses or any(type(n) is not int or not 100 <= n <= 599 for n in statuses):
            raise ValueError(message("statuses benötigt eine Liste mit HTTP-Statuscodes von 100 bis 599."))
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError(message("required_json_keys benötigt eine Liste mit Feldnamen."))
        if not isinstance(source.get("expect_json", {}), dict) or not isinstance(source.get("metric_fields", {}), dict):
            raise ValueError(message("expect_json und metric_fields benötigen Zuordnungen."))
    elif kind == "pihole":
        if "blocking_url" in source and validate_target(source["blocking_url"], endpoints, schemes) != origin:
            raise ValueError(message("DNS-Prüfungen müssen dieselbe Gegenstelle nutzen."))
        if "dns_file" in source:
            validate_snapshot(source["dns_file"], source.get("max_age", 180))
    elif kind == "snmp_volume":
        for field in ("job", "instance", "volume"):
            text_value(source.get(field), 200, field)
        if "health_metric" in source:
            name = source["health_metric"]
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
                raise ValueError(message("Ungültige Prometheus-Metrik für die Volume-Gesundheit."))


def validate_snapshot(name, age):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.json", name):
        raise ValueError(message("Die Messdatei muss einen einfachen Namen mit der Endung .json haben."))
    if type(age) not in {int, float} or age <= 0:
        raise ValueError(message("max_age muss eine positive Anzahl Sekunden sein."))


def validate_target(url, endpoints, schemes, *, origin_only=False):
    origin, _ = _endpoint(url, origin_only=origin_only)
    if origin not in endpoints or origin[0] not in schemes:
        raise ValueError(message("Das Ziel ist mit seinem Protokoll und Port in allowed_endpoints einzutragen."))
    return origin


def cert_subjects(hosts):
    subjects = ["DNS:localhost", "IP:127.0.0.1", "IP:::1"]
    for host in hosts:
        try:
            subjects.append(f"IP:{ipaddress.ip_address(host)}")
        except ValueError:
            label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            if len(host) > 253 or not re.fullmatch(rf"{label}(?:\.{label})*", host):
                raise ValueError(message("--host benötigt einen DNS-Namen oder eine IP-Adresse.")) from None
            subjects.append(f"DNS:{host}")
    return ",".join(dict.fromkeys(subjects))


def generate_local_tls(path, hosts=()):
    subject_names = cert_subjects(hosts)
    with TemporaryDirectory() as temporary:
        work = Path(temporary)

        def openssl(*arguments):
            subprocess.run(["openssl", *arguments], cwd=work, capture_output=True, check=True)

        openssl("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-sha256", "-days", "366",
                "-subj", "/CN=Landingpage Kit Local CA", "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", "ca.key", "-out", "ca.crt")
        openssl("req", "-newkey", "rsa:3072", "-nodes", "-sha256", "-subj", "/CN=localhost",
                "-keyout", "tls.key", "-out", "tls.csr")
        (work / "extensions").write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
            f"extendedKeyUsage=serverAuth\nsubjectAltName={subject_names}\n", encoding="ascii",
        )
        openssl("x509", "-req", "-in", "tls.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
                "-sha256", "-days", "365", "-extfile", "extensions", "-out", "tls.crt")
        bundle = work / "tls.pem"
        bundle.write_bytes(b"".join((work / name).read_bytes() for name in ("tls.key", "tls.crt", "ca.crt")))
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(bundle)
        ssl.create_default_context(cafile=bundle)
        replace_bytes(path, bundle.read_bytes())


def set_password(path, tls_path, *, use_stdin=False):
    if use_stdin:
        password = sys.stdin.read(1027).rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise ValueError(message("Für eine Eingabe ohne Terminal --stdin verwenden."))
        password = getpass(message("Neues Admin-Passwort: "))
        if password != getpass(message("Admin-Passwort wiederholen: ")):
            raise ValueError(message("Die beiden Passworteingaben stimmen nicht überein."))
    text_value(password, 1024, "Admin-Passwort")
    if len(password) < 16 or password != password.strip():
        raise ValueError(message("Mindestens 16 Zeichen und keine Leerzeichen am Anfang oder Ende verwenden."))
    if not tls_path.is_file():
        generate_local_tls(tls_path)
    replace_bytes(path, (password + "\n").encode("utf-8"))


def export_ca(path):
    certificates = re.findall(rb"-----BEGIN CERTIFICATE-----\r?\n.*?-----END CERTIFICATE-----\r?\n?",
                              path.read_bytes(), re.DOTALL)
    if len(certificates) < 2:
        raise ValueError(message("Das lokale CA-Zertifikat fehlt."))
    sys.stdout.buffer.write(certificates[-1])


def board_command(args, data_dir):
    doc = read_dashboard(sys.stdin.buffer)
    if args.command == "check":
        print(message("Dashboard-YAML ist gültig."))
        return
    if args.command == "init":
        if (data_dir / "dashboard.sqlite3").exists():
            raise ValueError(message("SQLite ist bereits vorhanden. Änderungen über preview und apply übernehmen."))
        seed = Path(os.environ.get("SEED_DIR", data_dir / "seed"))
        replace_bytes(seed / "seed.json", json_bytes(doc))
        print(message("Startkonfiguration gespeichert. Beim ersten Start wird sie in SQLite übernommen."))
        return
    api = Api()
    current = dashboard(api)
    expected = current.pop("revision")
    if args.command == "preview":
        print(message("Aktuelle Revision: {revision}", revision=expected))
        changes = list(difflib.unified_diff(pretty(current).splitlines(True), pretty(doc).splitlines(True),
                                          fromfile=message("gespeichert"), tofile="YAML"))
        sys.stdout.writelines(changes or [message("Dashboard ist bereits aktuell.\n")])
        return
    if args.revision != expected:
        raise ValueError(message("Die Revision hat sich geändert. Dashboard erneut mit preview prüfen."))
    if current == doc:
        print(message("Dashboard ist bereits aktuell. Revision {revision}.", revision=expected))
        return
    api.login()
    result = api.request("/api/dashboard", method="PUT", payload={**doc, "revision": expected})
    print(message("Dashboard gespeichert. Revision {revision}.", revision=result["revision"]))


def run(argv=None):
    cli = argparse.ArgumentParser(description=message(__doc__))
    commands = cli.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", message("Dashboard-YAML aus stdin prüfen")),
                            ("init", message("Dashboard-YAML vor dem ersten Start einrichten")),
                            ("preview", message("Dashboard-YAML mit der laufenden Version vergleichen")),
                            ("check-sources", message("Messquellen-YAML aus stdin prüfen")),
                            ("sources", message("Messquellen-YAML aus stdin speichern")),
                            ("certificate", message("Öffentliches lokales CA-Zertifikat ausgeben"))):
        commands.add_parser(name, help=help_text)
    commands.add_parser("apply", help=message("Dashboard-YAML in der überprüften Revision übernehmen")).add_argument(
        "--revision", required=True, type=int,
    )
    commands.add_parser("password", help=message("Admin-Passwort und beim ersten Mal lokales HTTPS einrichten")).add_argument(
        "--stdin", action="store_true", help=message("Eine Passwortzeile aus stdin lesen"),
    )
    commands.add_parser("tls-local", help=message("Lokales TLS-Zertifikat neu ausstellen")).add_argument(
        "--host", action="append", default=[], help=message("Zusätzlicher DNS-Name oder zusätzliche IP-Adresse"),
    )
    args = cli.parse_args(argv)
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    config = data_dir / "config"
    tls_file = Path(os.environ.get("TLS_CERT_FILE", config / "tls.pem"))
    if args.command in {"check", "init", "preview", "apply"}:
        board_command(args, data_dir)
    elif args.command in {"check-sources", "sources"}:
        doc = source_document(sys.stdin.buffer)
        if args.command == "sources":
            path = Path(os.environ.get("INTEGRATIONS_FILE", config / "integrations.json"))
            replace_bytes(path, json_bytes(doc))
            print(message("Messquellen gespeichert."))
        else:
            print(message("Messquellen-YAML ist gültig."))
    elif args.command == "password":
        set_password(Path(os.environ.get("ADMIN_PASSWORD_FILE", config / "admin-password")), tls_file,
                     use_stdin=args.stdin)
        print(message("Admin-Passwort eingerichtet. Bei laufendem Dienst ist ein Neustart erforderlich."))
    elif args.command == "tls-local":
        generate_local_tls(tls_file, args.host)
        print(message("Lokales TLS-Zertifikat eingerichtet. Bei laufendem Dienst ist ein Neustart erforderlich."))
    elif args.command == "certificate":
        export_ca(tls_file)


if __name__ == "__main__":
    try:
        run()
    except HTTPError as error:
        sys.exit(message("HTTP {code}. Vorschau, HTTPS-Einrichtung und passende Revision prüfen.", code=error.code))
    except (URLError, HTTPException):
        sys.exit(message("Der laufende Dienst ist nicht erreichbar. Containerstatus und TLS-Konfiguration prüfen."))
    except subprocess.CalledProcessError:
        sys.exit(message("OpenSSL konnte das lokale TLS-Zertifikat nicht erstellen."))
    except (RequestError, ProbeError) as error:
        sys.exit(error.localized(cli_language()))
    except (OSError, ValueError) as error:
        sys.exit(str(error))
