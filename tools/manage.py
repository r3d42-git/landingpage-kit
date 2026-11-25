"""Local administration client. Run inside the dashboard container."""

import argparse
import difflib
import http.cookiejar
import json
import os
from pathlib import Path
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request
import uuid


def pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


class Api:
    def __init__(self):
        cert = os.environ.get("TLS_CERT_FILE")
        handlers = [ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if cert and Path(cert).is_file():
            handlers.append(HTTPSHandler(context=ssl.create_default_context(cafile=cert)))
            self.base = "https://localhost:" + os.environ.get("TLS_PORT", "8443")
        else:
            self.base = "http://localhost:" + os.environ.get("PORT", "8080")
        self.client = build_opener(*handlers)

    def request(self, path, *, method="GET", payload=None, binary=False):
        headers = {"X-Dashboard-Request": "1", "Origin": self.base}
        if payload is not None and not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        elif payload is not None:
            headers["Content-Type"] = "application/zip"
        request = Request(self.base + path, data=payload, method=method, headers=headers)
        with self.client.open(request, timeout=25) as response:
            data = response.read()
            return data if binary else json.loads(data or b"{}")

    def login(self):
        path = os.environ.get("ADMIN_PASSWORD_FILE")
        if not path:
            raise ValueError("Kein Administrationskennwort konfiguriert.")
        self.request("/api/login", method="POST", payload={"password": Path(path).read_text().strip()})


def dashboard(api):
    data = api.request("/api/dashboard")
    return {k: data[k] for k in ("revision", "settings", "groups")}


def get_group(doc, group_id):
    for group in doc["groups"]:
        if group["id"] == group_id:
            return group
    raise ValueError("Kategorie nicht gefunden: " + group_id)


def get_item(doc, item_id):
    for group in doc["groups"]:
        for item in group["items"]:
            if item["id"] == item_id:
                return group, item
    raise ValueError("Eintrag nicht gefunden: " + item_id)


def fields(parser):
    for field in ("name", "url", "subtitle", "tag", "logo", "icon", "target", "source"):
        parser.add_argument("--" + field)
    parser.add_argument("--kind", choices=["link", "http", "service", "pihole", "nas", "youtube"])
    parser.add_argument("--check-link", action=argparse.BooleanOptionalAction, default=None,
                        help="Webseite alle fünf Minuten prüfen (Link-Kacheln)")


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "sources", "status", "backup"):
        commands.add_parser(name)
    apply = commands.add_parser("apply", help="Vollständiges Dashboard-JSON mit Revision aus stdin")
    apply.add_argument("--dry-run", action="store_true")
    restore = commands.add_parser("restore", help="Vorhandene Sicherung aus stdin wiederherstellen")
    restore.add_argument("--confirm", action="store_true", required=True)
    add = commands.add_parser("add")
    add.add_argument("group")
    fields(add)
    update = commands.add_parser("update")
    update.add_argument("item")
    fields(update)
    move = commands.add_parser("move")
    move.add_argument("item")
    move.add_argument("group")
    move.add_argument("--before", help="ID der nachfolgenden Kachel")
    remove = commands.add_parser("remove")
    remove.add_argument("item")
    remove.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    api = Api()
    cmd = args.command
    if cmd in ("sources", "status"):
        print(pretty(api.request("/api/" + cmd)), end="")
        return
    if cmd == "show":
        print(pretty(dashboard(api)), end="")
        return
    if cmd in ("backup", "restore"):
        api.login()
        if cmd == "backup":
            if sys.stdout.isatty():
                raise ValueError("ZIP-Ausgabe in eine Datei umleiten.")
            sys.stdout.buffer.write(api.request("/api/backup", binary=True))
        else:
            payload = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
            if len(payload) > 32 * 1024 * 1024:
                raise ValueError("Sicherung zu groß.")
            print(pretty(api.request("/api/restore", method="POST", payload=payload)), end="")
        return
    doc = dashboard(api)
    if cmd == "apply":
        proposed = json.load(sys.stdin)
        if proposed.get("revision") != doc["revision"]:
            raise ValueError("Revision veraltet. Änderungen mit einem frischen Export abgleichen.")
        if args.dry_run:
            sys.stdout.writelines(difflib.unified_diff(pretty(doc).splitlines(True), pretty(proposed).splitlines(True),
                                                      fromfile="gespeichert", tofile="vorgeschlagen"))
            return
        doc = proposed
    elif cmd in ("add", "update"):
        changes = {key: value for key, value in vars(args).items()
                   if key not in {"command", "group", "item"} and value is not None}
        if cmd == "add":
            if not changes.get("name") or not changes.get("url"):
                raise ValueError("Name und URL sind erforderlich.")
            changes.setdefault("kind", "link")
            changes["id"] = str(uuid.uuid4())
            get_group(doc, args.group)["items"].append(changes)
        else:
            item = get_item(doc, args.item)[1]
            item.update(changes)
            if item["kind"] != "youtube":
                item.pop("channels", None)
            if item["kind"] != "link" and args.check_link is None:
                item.pop("check_link", None)
    elif cmd == "move":
        old, item = get_item(doc, args.item)
        dest = get_group(doc, args.group)
        if args.before == args.item:
            raise ValueError("Diese Kachel ist bereits ausgewählt.")
        old["items"].remove(item)
        position = len(dest["items"])
        if args.before:
            ids = [entry["id"] for entry in dest["items"]]
            if args.before not in ids:
                raise ValueError("Die gewünschte Position gehört nicht zur Zielkategorie.")
            position = ids.index(args.before)
        dest["items"].insert(position, item)
    elif cmd == "remove":
        group, item = get_item(doc, args.item)
        group["items"].remove(item)
    api.login()
    result = api.request("/api/dashboard", method="PUT", payload=doc)
    print("Gespeichert, Revision", result.get("revision", doc["revision"] + 1))


if __name__ == "__main__":
    try:
        run()
    except HTTPError as error:
        try:
            description = json.loads(error.read()).get("error", "")
        except (ValueError, UnicodeError):
            description = ""
        sys.exit(f"HTTP {error.code} – {description}")
    except (ValueError, OSError, URLError) as error:
        sys.exit(str(error))
