"""HTTP API, dashboard storage and status polling for the local dashboard."""

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager, suppress
from dataclasses import dataclass
import hashlib
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import hmac
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import ssl
import stat
import threading
import time
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as ZlibError

import public_youtube
from localization import Text, encode_translations, phrase, render_reading, request_language, translate


ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
MAX_DOCUMENT = 1024 * 1024
MAX_ASSET = 3 * 1024 * 1024
MAX_ASSETS_SIZE = 24 * 1024 * 1024
MAX_ASSETS = 512
MAX_BACKUP = 32 * 1024 * 1024
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
ITEM_KINDS = {"link", "http", "pihole", "nas", "service", "youtube"}
STATUS_STATES = {"ok", "warning", "down", "unknown", "stale"}
INFRASTRUCTURE_PREFIX = "infrastructure:"
LINK_CHECK_INTERVAL = 300
COOKIE_NAME = "dashboard_session"
SESSION_SECONDS = 12 * 60 * 60
CONTENT_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: http: https:; font-src 'self' data:; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)
IMAGE_POLICY = "sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
ASSET_TYPES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
FIELD_NAMES = {
    "Abmeldung": "sign-out", "Adresse": "address", "Admin-Passwort": "admin password",
    "Anmeldung": "sign-in", "Bezeichnung": "label", "Einstellungen": "settings", "Gruppe": "group",
    "Gruppenname": "group name", "Kachel": "tile", "Kachelname": "tile name", "Kanaladresse": "channel address",
    "Kanalname": "channel name", "Kanalsuche": "channel lookup", "Passwort": "password", "Quelle": "source",
    "Quellenname": "source name", "Statusdetails": "status details", "Statuswert": "status value", "Titel": "title",
    "Untertitel": "subtitle", "YouTube-Kanal": "YouTube channel", "Zielname": "target name",
}

LINK_REACHABLE = Text("Webseite erreichbar", "Website reachable")
LINK_UNCONFIRMED = Text("Erreichbarkeit noch unbestätigt", "Reachability still unconfirmed")
LINK_UNREACHABLE = Text("Vom Server nicht erreichbar", "Unreachable from the server")
PROBE_TIMEOUT = Text("Zeitlimit überschritten", "Timed out")
PROBE_FAILED = Text("Statusabfrage fehlgeschlagen", "Status check failed")


class RequestError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST, **details):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details

    def localized(self, language):
        return translate(self.message, language)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, RecursionError):
        raise RequestError(Text("Ungültiges JSON.", "Invalid JSON.")) from None


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def field_name(label):
    return Text(label, FIELD_NAMES[label]) if label in FIELD_NAMES and not isinstance(label, Text) else label


def fields(value, allowed, required, label):
    if not isinstance(value, dict) or value.keys() - allowed or required - value.keys():
        raise RequestError(phrase("Ungültige Felder in {}.", "Invalid fields in {}.", field_name(label)))


def text_value(value, limit, label, *, blank=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not blank and not value.strip())
        or CONTROL_CHARACTERS.search(value)
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise RequestError(phrase("Ungültiger Text für {}.", "Invalid text for {}.", field_name(label)))
    return value


def identifier(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise RequestError(Text("Ungültige ID.", "Invalid ID."))
    return value


def url_value(value):
    value = text_value(value, 2048, "Adresse")
    if any(char.isspace() or char in '\\<>"`' for char in value):
        raise RequestError(Text("Ungültige Adresse.", "Invalid address."))
    try:
        url = urlsplit(value)
        if url.scheme in {"http", "https"} and url.hostname and url.username is None:
            if url.port is not None and not url.port:
                raise ValueError("invalid port")
            return value
        if value.startswith("/") and not value.startswith("//") and not url.scheme and not url.netloc:
            decoded_path = unquote(url.path, errors="strict")
            if not decoded_path.startswith("//") and "\\" not in decoded_path and not CONTROL_CHARACTERS.search(decoded_path):
                return value
    except ValueError:
        pass
    raise RequestError(Text("Adressen müssen mit http://, https:// oder einem einzelnen / beginnen.", "Addresses must start with http://, https:// or a single /."))


def validate_channels(channels):
    if not isinstance(channels, list) or len(channels) > public_youtube.MAX_CHANNELS:
        raise RequestError(phrase(
            "Pro Kachel sind bis zu {} YouTube-Kanäle möglich.",
            "Each tile supports up to {} YouTube channels.", public_youtube.MAX_CHANNELS,
        ))
    result = []
    seen = set()
    for channel in channels:
        fields(channel, {"id", "name"}, {"id", "name"}, "YouTube-Kanal")
        channel_id = channel["id"]
        if not isinstance(channel_id, str) or not public_youtube.CHANNEL_ID.fullmatch(channel_id):
            raise RequestError(Text("Ungültige YouTube-Kanal-ID.", "Invalid YouTube channel ID."))
        if channel_id in seen:
            raise RequestError(Text("Der YouTube-Kanal ist bereits eingetragen.", "This YouTube channel has already been added."))
        seen.add(channel_id)
        result.append({"id": channel_id, "name": text_value(channel["name"], 120, "Kanalname")})
    return result


def validate_dashboard(document, *, require_revision):
    fields(
        document,
        {"revision", "settings", "groups", "authenticated"},
        {"settings", "groups"} | ({"revision"} if require_revision else set()),
        "Dashboard",
    )
    if "revision" in document and (type(document["revision"]) is not int or document["revision"] < 1):
        raise RequestError(Text("Ungültige Revision.", "Invalid revision."))
    settings = document["settings"]
    fields(settings, {"title", "subtitle"}, {"title"}, "Einstellungen")
    settings = {
        "title": text_value(settings["title"], 160, "Titel"),
        "subtitle": text_value(settings.get("subtitle", ""), 400, "Untertitel", blank=True),
    }
    groups = document["groups"]
    if not isinstance(groups, list) or len(groups) > 30:
        raise RequestError(Text("Das Dashboard unterstützt bis zu 30 Gruppen.", "The dashboard supports up to 30 groups."))
    group_ids = set()
    item_ids = set()
    validated_groups = []
    for group in groups:
        fields(group, {"id", "name", "icon", "logo", "items"}, {"id", "name", "items"}, "Gruppe")
        group_id = identifier(group["id"])
        if group_id in group_ids:
            raise RequestError(Text("Eine Gruppen-ID kommt mehrfach vor.", "A group ID occurs more than once."))
        group_ids.add(group_id)
        if not isinstance(group["items"], list) or len(item_ids) + len(group["items"]) > 250:
            raise RequestError(Text("Das Dashboard unterstützt bis zu 250 Kacheln.", "The dashboard supports up to 250 tiles."))
        validated_group = {"id": group_id, "name": text_value(group["name"], 160, "Gruppenname"), "items": []}
        optional_fields(validated_group, group, {"icon": 120})
        for item in group["items"]:
            fields(
                item, {"id", "name", "url", "subtitle", "tag", "logo", "icon", "target", "kind", "source", "channels", "check_link"},
                {"id", "name", "url", "kind"}, "Kachel",
            )
            item_id = identifier(item["id"])
            if item_id in item_ids:
                raise RequestError(Text("Eine Kachel-ID kommt mehrfach vor.", "A tile ID occurs more than once."))
            item_ids.add(item_id)
            kind = item["kind"]
            if not isinstance(kind, str) or kind not in ITEM_KINDS:
                raise RequestError(Text("Unbekannter Kacheltyp.", "Unknown tile type."))
            validated_item = {
                "id": item_id,
                "name": text_value(item["name"], 160, "Kachelname"),
                "url": url_value(item["url"]),
                "kind": kind,
            }
            optional_fields(validated_item, item, {"subtitle": 400, "tag": 80, "icon": 120})
            if "check_link" in item:
                if kind != "link" or type(item["check_link"]) is not bool:
                    raise RequestError(Text("Die optionale Webprüfung gehört zu einer Link-Kachel.", "The optional website check requires a link tile."))
                if item["check_link"]:
                    if not urlsplit(validated_item["url"]).hostname:
                        raise RequestError(Text("Für die Webprüfung ist eine vollständige HTTP- oder HTTPS-Adresse erforderlich.", "The website check requires a full HTTP or HTTPS address."))
                    validated_item["check_link"] = True
            if kind == "youtube":
                validated_item["channels"] = validate_channels(item.get("channels", []))
            elif "channels" in item:
                raise RequestError(Text("Die Kanalliste gehört zu einer YouTube-Kachel.", "The channel list requires a YouTube tile."))
            target = item.get("target")
            if target not in (None, ""):
                if target not in ("_blank", "_self"):
                    raise RequestError(Text("Ungültiges Linkziel.", "Invalid link target."))
                validated_item["target"] = target
            if item.get("source") not in (None, ""):
                validated_item["source"] = identifier(item["source"])
            validated_group["items"].append(validated_item)
        validated_groups.append(validated_group)
    document = {"settings": settings, "groups": validated_groups}
    if len(json_bytes(document)) > MAX_DOCUMENT - 128:
        raise RequestError(Text("Das Dashboard ist zu groß.", "The dashboard is too large."))
    return document


def optional_fields(validated, source, text_fields):
    for key, limit in text_fields.items():
        if source.get(key) not in (None, ""):
            validated[key] = text_value(source[key], limit, key)
    if source.get("logo") not in (None, ""):
        validated["logo"] = url_value(source["logo"])


def asset_path(name):
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 240
        or CONTROL_CHARACTERS.search(name)
        or any(char in name for char in "\\:")
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise RequestError(Text("Ungültiger Pfad für ein Bild.", "Invalid image path."))
    return name


def validate_svg(data):
    lower = data.lower()
    if b"<!doctype" in lower or b"<!entity" in lower or b"<?xml-stylesheet" in lower:
        raise RequestError(Text("Das SVG enthält aktive oder externe Inhalte.", "The SVG contains active or external content."))
    try:
        root = ElementTree.fromstring(data.decode("utf-8"))
    except (UnicodeError, ElementTree.ParseError, ValueError):
        raise RequestError(Text("Ungültiges SVG.", "Invalid SVG.")) from None
    if root.tag not in {"svg", f"{SVG_NAMESPACE}svg"}:
        raise RequestError(Text("Ungültiges SVG.", "Invalid SVG."))
    forbidden = {"animate", "animatemotion", "animatetransform", "foreignobject", "iframe", "script", "set"}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in forbidden:
            raise RequestError(Text("Das SVG enthält aktive oder externe Inhalte.", "The SVG contains active or external content."))
        values = list(element.attrib.values())
        if tag == "style" and element.text:
            values.append(element.text)
        for key, value in element.attrib.items():
            attribute = key.rsplit("}", 1)[-1].lower()
            if attribute.startswith("on") or (attribute in {"href", "src"} and not value.startswith("#")):
                raise RequestError(Text("Das SVG enthält aktive oder externe Inhalte.", "The SVG contains active or external content."))
        for value in values:
            if re.search(r"@import|url\(\s*['\"]?\s*[^#'\")\s]", value, re.IGNORECASE):
                raise RequestError(Text("Das SVG enthält aktive oder externe Inhalte.", "The SVG contains active or external content."))


def validate_asset(name, data):
    suffix = Path(asset_path(name)).suffix.lower()
    is_image = {
        ".avif": lambda: data[4:12] in {b"ftypavif", b"ftypavis"},
        ".bmp": lambda: data.startswith(b"BM"),
        ".gif": lambda: data.startswith((b"GIF87a", b"GIF89a")),
        ".ico": lambda: data.startswith(b"\x00\x00\x01\x00"),
        ".jpeg": lambda: data.startswith(b"\xff\xd8\xff"),
        ".jpg": lambda: data.startswith(b"\xff\xd8\xff"),
        ".png": lambda: data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".svg": lambda: True,
        ".webp": lambda: data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not 0 < len(data) <= MAX_ASSET or suffix not in is_image or not is_image[suffix]():
        raise RequestError(Text("Ungültiges oder zu großes Bild.", "The image is invalid or too large."))
    if suffix == ".svg":
        validate_svg(data)


def validate_asset_budget(count, size):
    if count > MAX_ASSETS or size > MAX_ASSETS_SIZE:
        raise RequestError(Text("Das Archiv enthält zu viele oder zu große Bilder.", "The archive contains too many images or images that are too large."))


def read_seed(seed_dir):
    seed_file = seed_dir / "seed.json"
    if not seed_file.is_file():
        return {"settings": {"title": "Landingpage", "subtitle": ""}, "groups": []}, {}
    with seed_file.open("rb") as file:
        raw_document = file.read(MAX_DOCUMENT + 1)
    if len(raw_document) > MAX_DOCUMENT:
        raise RequestError(Text("Die Startkonfiguration ist zu groß.", "The initial configuration is too large."))
    document = validate_dashboard(parse_json(raw_document), require_revision=False)
    assets_dir = seed_dir / "assets"
    assets = {}
    total_size = 0
    for path in sorted(assets_dir.rglob("*")):
        if path.is_symlink():
            raise RequestError(Text("Symbolische Links sind für mitgelieferte Bilder ungültig.", "Bundled images cannot use symbolic links."))
        if not path.is_file() or path.name.startswith("."):
            continue
        name = path.relative_to(assets_dir).as_posix()
        with path.open("rb") as file:
            data = file.read(MAX_ASSET + 1)
        validate_asset(name, data)
        total_size += len(data)
        validate_asset_budget(len(assets) + 1, total_size)
        assets[name] = data
    return document, assets


def items_by_id(document):
    return {item["id"]: item for group in document["groups"] for item in group["items"]}


def fingerprint(item):
    fields = [item["kind"], item["url"], item.get("source")]
    if item["kind"] == "link" and item.get("check_link"):
        fields.append(True)
    if item["kind"] == "youtube":
        fields.append([channel["id"] for channel in item.get("channels", [])])
    return json_bytes(fields).decode("utf-8")


def monitored(item):
    if item["kind"] == "youtube":
        return bool(item.get("channels"))
    return item["kind"] != "link" or bool(item.get("check_link"))


class Store:
    def __init__(self, data_dir, seed_dir):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "dashboard.sqlite3"
        self.archive_slot = threading.Lock()
        with closing(self.connection()) as database:
            database.execute("PRAGMA journal_mode = WAL")
            database.executescript("""
                CREATE TABLE IF NOT EXISTS dashboard (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    revision INTEGER NOT NULL,
                    document BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    name TEXT PRIMARY KEY,
                    data BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS statuses (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
            """)
            with database:
                database.execute("BEGIN IMMEDIATE")
                if database.execute("SELECT 1 FROM dashboard").fetchone() is None:
                    document, assets = read_seed(seed_dir)
                    database.execute("INSERT INTO dashboard VALUES (1, 1, ?)", (json_bytes(document),))
                    self.replace_assets(database, assets)

    def connection(self):
        return sqlite3.connect(self.path, timeout=5)

    @contextmanager
    def archive_operation(self):
        if not self.archive_slot.acquire(blocking=False):
            raise RequestError(Text("Ein Backup-Vorgang läuft bereits.", "A backup operation is already in progress."), HTTPStatus.CONFLICT)
        try:
            yield
        finally:
            self.archive_slot.release()

    @staticmethod
    def read_dashboard(database):
        revision, document = database.execute("SELECT revision, document FROM dashboard WHERE id = 1").fetchone()
        return {"revision": revision, **json.loads(document)}

    def dashboard(self):
        with closing(self.connection()) as database:
            return self.read_dashboard(database)

    @staticmethod
    def replace_assets(database, assets):
        database.execute("DELETE FROM assets")
        database.executemany("INSERT INTO assets VALUES (?, ?)", assets.items())

    def asset(self, name):
        with closing(self.connection()) as database:
            result = database.execute("SELECT data FROM assets WHERE name = ?", (name,)).fetchone()
        return result[0] if result else None

    def save(self, document, *, expected_revision=None, assets=None):
        with closing(self.connection()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            current = self.read_dashboard(database)
            if expected_revision is not None and current["revision"] != expected_revision:
                raise RequestError(
                    Text("Das Dashboard wurde zwischenzeitlich geändert.", "The dashboard has changed since you opened it."),
                    HTTPStatus.CONFLICT, revision=current["revision"],
                )
            revision = current["revision"] + 1
            database.execute("UPDATE dashboard SET revision = ?, document = ? WHERE id = 1", (revision, json_bytes(document)))
            current_items = items_by_id(document)
            expired_ids = [
                (item_id,)
                for item_id, signature in database.execute("SELECT id, fingerprint FROM statuses")
                if not item_id.startswith(INFRASTRUCTURE_PREFIX)
                and (item_id not in current_items or fingerprint(current_items[item_id]) != signature)
            ]
            database.executemany("DELETE FROM statuses WHERE id = ?", expired_ids)
            if assets is not None:
                self.replace_assets(database, assets)
        return {"revision": revision, **document}

    def read_statuses(self):
        with closing(self.connection()) as database, database:
            database.execute("BEGIN")
            document = self.read_dashboard(database)
            cached = {item_id: (signature, json.loads(payload)) for item_id, signature, payload in database.execute(
                "SELECT id, fingerprint, payload FROM statuses"
            )}
        return items_by_id(document), cached

    def write_status(self, item_id, signature, result, checked_at, stale_after):
        with closing(self.connection()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            current = None
            if not item_id.startswith(INFRASTRUCTURE_PREFIX):
                current = items_by_id(self.read_dashboard(database)).get(item_id)
                if current is None or fingerprint(current) != signature:
                    return
            cached = database.execute("SELECT payload FROM statuses WHERE id = ? AND fingerprint = ?", (item_id, signature)).fetchone()
            previous = json.loads(cached[0]) if cached else {}
            last_success = previous.get("last_success")
            observed_at = result.get("observed_at", checked_at)
            measurement_max_age = result.get("measurement_max_age", stale_after)
            if result["state"] in {"ok", "warning"} and checked_at - observed_at <= measurement_max_age:
                last_success = max(last_success or 0, observed_at)
            payload = {**result, "checked_at": checked_at, "last_success": last_success, "localized": True}
            if current and current.get("check_link"):
                failures = min(2, previous.get("failures", 0) + 1) if result["state"] == "down" else 0
                payload["failures"] = failures
                if failures == 1:
                    if last_success and previous.get("state") == "ok":
                        payload.update(state="ok", label=LINK_REACHABLE)
                    else:
                        payload.update(state="unknown", label=LINK_UNCONFIRMED)
                elif failures == 2:
                    payload["label"] = LINK_UNREACHABLE
            if (current and current["kind"] == "nas" and result["state"] in {"stale", "unknown"}
                    and not result.get("metrics") and last_success and previous.get("metrics")):
                payload.update(state="stale", metrics=previous["metrics"], observed_at=last_success)
                if "progress" in previous:
                    payload["progress"] = previous["progress"]
            database.execute("""
                INSERT INTO statuses VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET fingerprint = excluded.fingerprint, payload = excluded.payload
            """, (item_id, signature, json_bytes(encode_translations(payload))))

    def backup(self):
        with closing(self.connection()) as database, database, BytesIO() as output:
            database.execute("BEGIN")
            document = {"schema_version": SCHEMA_VERSION, "dashboard": self.read_dashboard(database)}
            with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
                archive.writestr("dashboard.json", json_bytes(document))
                for name, data in database.execute("SELECT name, data FROM assets ORDER BY name"):
                    archive.writestr(f"assets/{name}", data)
            return output.getvalue()


def read_backup(data):
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2 * MAX_ASSETS + 1:
                raise RequestError(Text("Das Backup enthält zu viele Einträge.", "The backup contains too many entries."))
            names = set()
            assets = {}
            document = None
            total_size = 0
            for entry in entries:
                name = entry.orig_filename
                if name in names or entry.filename != name or entry.flag_bits & 1 or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise RequestError(Text("Ungültiger Eintrag im Backup.", "Invalid entry in the backup."))
                names.add(name)
                file_type = stat.S_IFMT(entry.external_attr >> 16)
                if entry.is_dir():
                    if name != "assets/":
                        if not name.startswith("assets/"):
                            raise RequestError(Text("Unerwarteter Ordner im Backup.", "Unexpected folder in the backup."))
                        asset_path(name[len("assets/"):-1])
                    if entry.file_size or file_type not in {0, stat.S_IFDIR}:
                        raise RequestError(Text("Ungültiger Ordner im Backup.", "Invalid folder in the backup."))
                    continue
                if file_type not in {0, stat.S_IFREG}:
                    raise RequestError(Text("Ungültiger Dateityp im Backup.", "Invalid file type in the backup."))
                if name == "dashboard.json":
                    if entry.file_size > MAX_DOCUMENT:
                        raise RequestError(Text("Die Dashboard-Datei ist zu groß.", "The dashboard file is too large."))
                    manifest = parse_json(archive.read(entry))
                    fields(manifest, {"schema_version", "dashboard"}, {"schema_version", "dashboard"}, "Backup")
                    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
                        raise RequestError(Text("Diese Backup-Version wird nicht unterstützt.", "This backup version is unsupported."))
                    document = validate_dashboard(manifest["dashboard"], require_revision=True)
                elif name.startswith("assets/"):
                    image_name = asset_path(name[len("assets/"):])
                    total_size += entry.file_size
                    validate_asset_budget(len(assets) + 1, total_size)
                    if not 0 < entry.file_size <= MAX_ASSET:
                        raise RequestError(Text("Ungültige Bildgröße im Backup.", "Invalid image size in the backup."))
                    image = archive.read(entry)
                    validate_asset(image_name, image)
                    assets[image_name] = image
                else:
                    raise RequestError(Text("Unerwartete Datei im Backup.", "Unexpected file in the backup."))
            if document is None:
                raise RequestError(Text("Im Backup fehlt dashboard.json.", "The backup is missing dashboard.json."))
            return document, assets
    except (BadZipFile, EOFError, NotImplementedError, RuntimeError, OSError, UnicodeError, ZlibError):
        raise RequestError(Text("Ungültiges ZIP-Backup.", "Invalid ZIP backup.")) from None


def unknown_status(label=Text("Noch nicht geprüft", "Not checked yet")):
    return {"state": "unknown", "label": label, "metrics": []}


def clean_status(result, now):
    if not isinstance(result, dict):
        raise RequestError(Text("Ungültiger Status.", "Invalid status."))
    state = result.get("state")
    if not isinstance(state, str) or state not in STATUS_STATES:
        raise RequestError(Text("Ungültiger Status.", "Invalid status."))
    status = {"state": state, "label": text_value(result.get("label"), 160, "Status"), "metrics": []}
    video_id = result.get("latest_video_id")
    if isinstance(video_id, str) and public_youtube.VIDEO_ID.fullmatch(video_id):
        status["latest_video_id"] = video_id
    metrics = result.get("metrics", [])
    if not isinstance(metrics, list) or len(metrics) > 12:
        raise RequestError(Text("Ungültige Statuswerte.", "Invalid status values."))
    for metric in metrics:
        fields(metric, {"label", "value"}, {"label", "value"}, "Statuswert")
        value = metric["value"]
        if isinstance(value, str):
            value = text_value(value, 160, "Statuswert", blank=True)
        elif type(value) not in {int, float} or not math.isfinite(value):
            raise RequestError(Text("Ungültiger Statuswert.", "Invalid status value."))
        status["metrics"].append({"label": text_value(metric["label"], 100, "Bezeichnung"), "value": value})
    if result.get("detail") not in (None, ""):
        status["detail"] = text_value(result["detail"], 500, "Statusdetails")
    progress = result.get("progress")
    if type(progress) in {int, float} and 0 <= progress <= 100:
        status["progress"] = progress
    observed = result.get("observed_at")
    if type(observed) in {int, float} and 0 < observed <= now + 60:
        status["observed_at"] = min(int(observed), now)
        max_age = result.get("measurement_max_age")
        if type(max_age) in {int, float} and 1 <= max_age <= 86400:
            status["measurement_max_age"] = int(max_age)
    return status


@dataclass
class ProbeJob:
    signature: str
    future: Future
    deadline: float
    timed_out: bool = False


class StatusPoller:
    def __init__(self, store, provider, *, interval=60, stale_after=180, timeout=10, workers=6, infrastructure=None):
        self.store = store
        self.provider = provider
        self.interval = interval
        self.stale_after = stale_after
        self.timeout = timeout
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dashboard-probe")
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self.run, name="dashboard-status", daemon=True)
        self.active = {}
        self.next_due = {}
        items, cached = self.store.read_statuses()
        now, wall_time = time.monotonic(), time.time()
        for item_id, (signature, status) in cached.items():
            item = items.get(item_id)
            if item and item.get("check_link") and signature == fingerprint(item):
                elapsed = max(0, wall_time - status["checked_at"])
                self.next_due[(item_id, signature)] = now + max(0, LINK_CHECK_INTERVAL - elapsed)
        self.infrastructure = identifier(infrastructure) if infrastructure else None

    def infrastructure_item(self):
        return {"id": f"infra-{self.infrastructure}", "kind": "service", "source": self.infrastructure, "url": "/"}

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread.is_alive():
            self.thread.join()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except sqlite3.Error:
                logging.exception("Statusspeicher konnte nicht aktualisiert werden")
            self.wake.wait(0.5)
            self.wake.clear()

    def tick(self):
        now = time.monotonic()
        items = {
            item_id: (fingerprint(item), item)
            for item_id, item in items_by_id(self.store.dashboard()).items()
            if monitored(item)
        }
        if self.infrastructure:
            item = self.infrastructure_item()
            items[INFRASTRUCTURE_PREFIX + self.infrastructure] = (fingerprint(item), item)
        current_keys = {(item_id, signature) for item_id, (signature, _) in items.items()}
        self.next_due = {key: due for key, due in self.next_due.items() if key in current_keys}
        for item_id, job in list(self.active.items()):
            key = (item_id, job.signature)
            if job.future.done():
                del self.active[item_id]
                if job.timed_out:
                    continue
            elif not job.timed_out and now >= job.deadline:
                job.timed_out = True
            else:
                continue
            if key in current_keys:
                item = items[item_id][1]
                self.record(item_id, job, link_check=item.get("check_link", False))
                interval = LINK_CHECK_INTERVAL if item.get("check_link") else self.interval
                self.next_due[key] = now + interval
        sorted_items = sorted(items.items(), key=lambda pair: self.next_due.get((pair[0], pair[1][0]), 0))
        for item_id, (signature, item) in sorted_items:
            if len(self.active) >= self.workers:
                break
            if item_id in self.active or self.next_due.get((item_id, signature), 0) > now:
                continue
            future = self.pool.submit(self.probe, item)
            self.active[item_id] = ProbeJob(signature, future, now + self.timeout)
            future.add_done_callback(lambda _future: self.wake.set())

    def probe(self, item):
        result = self.provider.probe(item)
        return time.monotonic(), result

    def record(self, item_id, job, *, link_check):
        now = int(time.time())
        status = unknown_status(PROBE_TIMEOUT)
        if link_check:
            status["state"] = "down"
        if not job.timed_out:
            try:
                completed_at, result = job.future.result()
                if completed_at <= job.deadline:
                    status = clean_status(result, now)
            except Exception as error:
                logging.warning("Statusabfrage fehlgeschlagen (%s)", type(error).__name__)
                status = unknown_status(PROBE_FAILED)
        self.store.write_status(item_id, job.signature, status, now, self.stale_after)

    def snapshot(self, now=None, *, language="de"):
        if now is None:
            now = int(time.time())
        items, cached = self.store.read_statuses()
        def status(item_id, item):
            signature, payload = cached.get(item_id, (None, None))
            if signature != fingerprint(item):
                return render_reading({**unknown_status(), "checked_at": 0, "last_success": None}, language)
            if not payload.pop("localized", False):
                if item["kind"] == "youtube":
                    public_youtube.localize_legacy(payload)
                elif adapter := getattr(self.provider, "localize_legacy", None):
                    adapter(item, payload)
                if item.get("check_link") and payload.get("failures"):
                    for label in (LINK_REACHABLE, LINK_UNCONFIRMED, LINK_UNREACHABLE):
                        if payload["label"] == label:
                            payload["label"] = label
                            break
            observed_at = payload.pop("observed_at", payload["checked_at"])
            poll_max_age = max(2 * LINK_CHECK_INTERVAL, self.stale_after) if item.get("check_link") else self.stale_after
            max_age = payload.pop("measurement_max_age", poll_max_age)
            if now - observed_at > max_age or now - payload["checked_at"] > poll_max_age:
                payload["state"] = "stale"
                payload["label"] = Text("Veraltet", "Stale")
            return render_reading(payload, language)

        result = {"items": {item_id: status(item_id, item) for item_id, item in items.items() if monitored(item)}}
        if self.infrastructure:
            infrastructure_status = status(INFRASTRUCTURE_PREFIX + self.infrastructure, self.infrastructure_item())
            result["infrastructure"] = {self.infrastructure: infrastructure_status}
        return result

    def sources(self):
        sources = []
        for source in self.provider.sources():
            to_display = {
                "id": identifier(source["id"]),
                "label": text_value(source["label"], 160, "Quelle"),
                "kind": identifier(source["kind"]),
            }
            sources.append(to_display)
        return {"sources": sources}


class Sessions:
    def __init__(self, password, secure):
        self.password_digest = hashlib.sha256(password).digest() if password is not None else None
        self.secure = secure
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.Lock()

    def login(self, password, address):
        if self.password_digest is None:
            raise RequestError(Text("Die Bearbeitung ist nicht eingerichtet.", "Editing is not configured."), HTTPStatus.SERVICE_UNAVAILABLE)
        now = time.monotonic()
        with self.lock:
            self.attempts = {
                ip: (count, until) for ip, (count, until) in self.attempts.items() if until > now
            }
            count, until = self.attempts.get(address, (0, now + 300))
            if count >= 8 or (address not in self.attempts and len(self.attempts) >= 1024):
                raise RequestError(Text("Zu viele Anmeldeversuche. Bitte später erneut versuchen.", "Too many sign-in attempts. Try again later."), HTTPStatus.TOO_MANY_REQUESTS)
            candidate_digest = hashlib.sha256(password.encode("utf-8")).digest()
            if not hmac.compare_digest(self.password_digest, candidate_digest):
                self.attempts[address] = (count + 1, until)
                raise RequestError(Text("Falsches Passwort.", "Incorrect password."), HTTPStatus.UNAUTHORIZED)
            self.attempts.pop(address, None)
            self.sessions = {token: until for token, until in self.sessions.items() if until > now}
            if len(self.sessions) >= 128:
                del self.sessions[next(iter(self.sessions))]
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + SESSION_SECONDS
            return token

    def authenticated(self, token):
        with self.lock:
            return self.sessions.get(token, 0) > time.monotonic()

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def cookie(self, token):
        cookie = SimpleCookie()
        cookie[COOKIE_NAME] = token
        cookie[COOKIE_NAME]["path"] = "/"
        cookie[COOKIE_NAME]["max-age"] = SESSION_SECONDS if token else 0
        cookie[COOKIE_NAME]["httponly"] = True
        cookie[COOKIE_NAME]["samesite"] = "Strict"
        cookie[COOKIE_NAME]["secure"] = self.secure
        return cookie[COOKIE_NAME].OutputString()


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, address, *, store, poller, sessions, web_dir, origin=None, tls=None, read_only=False, edit_url=None):
        self.store = store
        self.poller = poller
        self.sessions = sessions
        self.web_dir = web_dir.resolve()
        self.origin = origin
        self.tls = tls
        self.read_only = read_only
        self.edit_url = edit_url
        self.request_slots = threading.BoundedSemaphore(32)
        super().__init__(address, DashboardHandler)

    def get_request(self):
        request, address = super().get_request()
        if self.tls is not None:
            try:
                request = self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
            except OSError:
                request.close()
                raise
        return request, address

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


def canonical_origin(value):
    try:
        if any(char.isspace() or char in "\\" for char in value):
            return None
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None:
            return None
        if url.path or url.query or url.fragment or url.port == 0 or CONTROL_CHARACTERS.search(value):
            return None
        port = url.port or (443 if url.scheme == "https" else 80)
        return url.scheme, url.hostname, port
    except ValueError:
        return None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "Dashboard"
    timeout = 12

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.handle_http()

    def do_HEAD(self):
        self.handle_http()

    def do_POST(self):
        self.handle_http()

    def do_PUT(self):
        self.handle_http()

    def handle_http(self):
        self.language = request_language(",".join(self.headers.get_all("Accept-Language", [])))
        try:
            path = unquote(urlsplit(self.path).path, errors="strict")
            if self.command in {"GET", "HEAD"}:
                self.read_route(path)
            else:
                self.write_route(path)
        except RequestError as error:
            self.send_error_json(error)
        except (UnicodeError, ValueError):
            self.send_error_json(RequestError(Text("Ungültige Anfrage.", "Invalid request.")))
        except (TimeoutError, BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            logging.exception("Dashboard-Anfrage fehlgeschlagen")
            self.send_error_json(RequestError(
                Text("Die Anfrage konnte nicht verarbeitet werden.", "The request could not be processed."),
                HTTPStatus.INTERNAL_SERVER_ERROR,
            ))

    def send_error_json(self, error):
        data = {"error": error.localized(self.language), **error.details}
        if isinstance(error.message, Text):
            data["error_translations"] = {language: error.localized(language) for language in ("de", "en")}
        self.send_json(data, error.status)

    def token(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else ""

    def require_admin(self):
        self.require_write_access()
        if not self.server.sessions.authenticated(self.token()):
            raise RequestError(Text("Anmeldung erforderlich.", "Sign-in required."), HTTPStatus.UNAUTHORIZED)

    def require_write_access(self):
        if self.server.read_only:
            raise RequestError(Text("Die Bearbeitung ist über HTTPS verfügbar.", "Editing is available over HTTPS."), HTTPStatus.FORBIDDEN)

    def require_mutation_header(self):
        self.require_write_access()
        origin_headers = self.headers.get_all("Origin", [])
        if self.headers.get_all("X-Dashboard-Request", []) != ["1"] or len(origin_headers) > 1:
            raise RequestError(Text("Anfrage nicht erlaubt.", "Request not allowed."), HTTPStatus.FORBIDDEN)
        if origin_headers:
            scheme = "https" if self.server.sessions.secure else "http"
            expected = self.server.origin or f"{scheme}://{self.headers.get('Host', '')}"
            origin = canonical_origin(origin_headers[0])
            if origin is None or origin != canonical_origin(expected):
                raise RequestError(Text("Die Anfrage stammt von einer anderen Website.", "The request came from a different website."), HTTPStatus.FORBIDDEN)

    def request_body(self, maximum, content_types):
        if self.headers.get("Transfer-Encoding") is not None:
            raise RequestError(Text("Anfragen mit Transfer-Encoding werden nicht unterstützt.", "Requests with Transfer-Encoding are unsupported."))
        if self.headers.get_content_type() not in content_types:
            raise RequestError(Text("Ungültiger Content-Type.", "Invalid Content-Type."), HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,9}", lengths[0]):
            raise RequestError(Text("Content-Length fehlt oder ist ungültig.", "Content-Length is missing or invalid."), HTTPStatus.LENGTH_REQUIRED)
        size = int(lengths[0])
        if size < 1:
            raise RequestError(Text("Die Anfrage enthält keine Daten.", "The request contains no data."))
        if size > maximum:
            raise RequestError(Text("Die Anfrage ist zu groß.", "The request is too large."), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(size)
        if len(body) != size:
            raise RequestError(Text("Die Anfrage ist unvollständig.", "The request is incomplete."))
        return body

    def json_request(self, maximum=MAX_DOCUMENT):
        return parse_json(self.request_body(maximum, {"application/json"}))

    def read_route(self, path):
        if path == "/healthz":
            self.server.store.dashboard()
            self.send_json({"ok": True})
        elif path == "/api/dashboard":
            dashboard = self.server.store.dashboard()
            dashboard["authenticated"] = not self.server.read_only and self.server.sessions.authenticated(self.token())
            if self.server.edit_url:
                dashboard["edit_url"] = self.server.edit_url
            self.send_json(dashboard)
        elif path == "/api/sources":
            self.send_json(self.server.poller.sources())
        elif path == "/api/status":
            self.send_json(self.server.poller.snapshot(language=self.language))
        elif path == "/api/backup":
            self.require_admin()
            export_date = time.strftime("%Y-%m-%d")
            with self.server.store.archive_operation():
                self.send_data(
                    self.server.store.backup(), "application/zip",
                    headers={"Content-Disposition": f'attachment; filename="dashboard-{export_date}.zip"'},
                )
        elif path.startswith("/assets/"):
            try:
                name = asset_path(path[len("/assets/"):])
            except RequestError:
                raise RequestError(Text("Bild nicht gefunden.", "Image not found."), HTTPStatus.NOT_FOUND) from None
            data = self.server.store.asset(name)
            if data is None:
                raise RequestError(Text("Bild nicht gefunden.", "Image not found."), HTTPStatus.NOT_FOUND)
            self.send_data(data, ASSET_TYPES[Path(name).suffix.lower()], policy=IMAGE_POLICY)
        elif path == "/api" or path.startswith("/api/"):
            raise RequestError(Text("Endpunkt nicht gefunden.", "Endpoint not found."), HTTPStatus.NOT_FOUND)
        else:
            self.send_static(path)

    def write_route(self, path):
        route = self.command, path
        if route == ("POST", "/api/login"):
            self.require_mutation_header()
            body = self.json_request(8192)
            fields(body, {"password"}, {"password"}, "Anmeldung")
            password = text_value(body["password"], 1024, "Passwort")
            token = self.server.sessions.login(password, self.client_address[0])
            self.server.sessions.logout(self.token())
            self.send_json({"authenticated": True}, headers={"Set-Cookie": self.server.sessions.cookie(token)})
        elif route == ("POST", "/api/logout"):
            self.require_mutation_header()
            fields(self.json_request(128), set(), set(), "Abmeldung")
            self.server.sessions.logout(self.token())
            self.send_json({"authenticated": False}, headers={"Set-Cookie": self.server.sessions.cookie("")})
        elif route == ("POST", "/api/youtube/resolve"):
            self.require_mutation_header()
            self.require_admin()
            body = self.json_request(4096)
            fields(body, {"input"}, {"input"}, "Kanalsuche")
            query = text_value(body["input"], 2048, "Kanaladresse")
            try:
                channel = public_youtube.resolve_channel(query)
            except ValueError as error:
                message = error.args[0] if len(error.args) == 1 else str(error)
                raise RequestError(message) from None
            self.send_json({"channel": channel})
        elif route == ("PUT", "/api/dashboard"):
            self.require_mutation_header()
            self.require_admin()
            body = self.json_request()
            document = validate_dashboard(body, require_revision=True)
            updated = self.server.store.save(document, expected_revision=body["revision"])
            self.server.poller.wake.set()
            self.send_json({**updated, "authenticated": True})
        elif route == ("POST", "/api/restore"):
            self.require_mutation_header()
            self.require_admin()
            with self.server.store.archive_operation():
                archive = self.request_body(MAX_BACKUP, {"application/zip", "application/octet-stream"})
                document, assets = read_backup(archive)
                updated = self.server.store.save(document, assets=assets)
            self.server.poller.wake.set()
            self.send_json({**updated, "authenticated": True})
        else:
            raise RequestError(Text("Endpunkt nicht gefunden.", "Endpoint not found."), HTTPStatus.NOT_FOUND)

    def send_static(self, path):
        name = "index.html" if path == "/" else path.removeprefix("/")
        target = (self.server.web_dir / name).resolve()
        if not target.is_relative_to(self.server.web_dir) or not target.is_file():
            raise RequestError(Text("Datei nicht gefunden.", "File not found."), HTTPStatus.NOT_FOUND)
        if target.suffix == ".js":
            media_type = "text/javascript"
        else:
            media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if media_type.startswith("text/"):
            media_type += "; charset=utf-8"
        referrer_policy = "same-origin" if name == "index.html" else "no-referrer"
        self.send_data(target.read_bytes(), media_type, policy=CONTENT_POLICY, referrer_policy=referrer_policy)

    def send_json(self, data, status=HTTPStatus.OK, *, headers=None):
        self.send_data(json_bytes(data), "application/json; charset=utf-8", status, headers={
            "Content-Language": self.language, "Vary": "Accept-Language", **(headers or {}),
        })

    def send_data(self, data, media_type, status=HTTPStatus.OK, *, headers=None,
                  policy="default-src 'none'; frame-ancestors 'none'", referrer_policy="no-referrer"):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Referrer-Policy", referrer_policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(data)


def admin_password():
    password_file = os.environ.get("ADMIN_PASSWORD_FILE")
    if not password_file:
        logging.warning("ADMIN_PASSWORD_FILE fehlt. Das Dashboard startet im Lesemodus.")
        return None
    with Path(password_file).open("rb") as file:
        raw = file.read(4097)
    try:
        if len(raw) > 4096:
            raise RequestError(Text("Passwortdatei ist zu groß.", "The password file is too large."))
        password = text_value(raw.rstrip(b"\r\n").decode("utf-8"), 1024, "Admin-Passwort")
    except (UnicodeError, RequestError):
        raise SystemExit("ADMIN_PASSWORD_FILE enthält kein gültiges Passwort.") from None
    return password.encode("utf-8")


def tls_context(cert_file, key_file):
    if not cert_file and not key_file:
        return None
    if not cert_file or not key_file:
        raise SystemExit("TLS_CERT_FILE und TLS_KEY_FILE müssen gemeinsam gesetzt sein.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file, key_file)
    return context


def configured_edit_url(value):
    if not value:
        return None
    try:
        value = url_value(value)
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.path == "/" and parsed.query == "edit=1" and not parsed.fragment:
            return value
    except RequestError:
        pass
    raise SystemExit("DASHBOARD_EDIT_URL muss eine HTTPS-Adresse mit /?edit=1 sein.")


def main():
    import integrations

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web_dir = Path(os.environ.get("WEB_DIR", ROOT / "web" / "dist"))
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    seed_dir = Path(os.environ.get("SEED_DIR", ROOT / "seed"))
    origin = os.environ.get("DASHBOARD_ORIGIN")
    if origin and canonical_origin(origin) is None:
        raise SystemExit("DASHBOARD_ORIGIN muss eine HTTP- oder HTTPS-Origin ohne Pfad sein.")
    tls = tls_context(os.environ.get("TLS_CERT_FILE"), os.environ.get("TLS_KEY_FILE"))
    edit_url = configured_edit_url(os.environ.get("DASHBOARD_EDIT_URL"))
    secure_cookie = tls is not None or os.environ.get("COOKIE_SECURE") == "1" or bool(origin and origin.startswith("https://"))
    interval = max(5, int(os.environ.get("POLL_INTERVAL_SECONDS", "60")))
    stale_after = max(interval, int(os.environ.get("STATUS_STALE_SECONDS", str(max(180, 3 * interval)))))
    store = Store(data_dir, seed_dir)
    poller = StatusPoller(
        store, integrations, interval=interval, stale_after=stale_after,
        infrastructure=os.environ.get("INFRASTRUCTURE_SOURCE"),
    )
    sessions = Sessions(admin_password(), secure_cookie)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    common = {"store": store, "poller": poller, "sessions": sessions, "web_dir": web_dir, "origin": origin}

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    with ExitStack() as servers:
        server = servers.enter_context(DashboardServer((host, port), **common, read_only=tls is not None, edit_url=edit_url))
        if tls is not None:
            tls_port = int(os.environ.get("TLS_PORT", "8443"))
            admin_server = servers.enter_context(DashboardServer((host, tls_port), **common, tls=tls))
            admin_thread = threading.Thread(target=admin_server.serve_forever, name="dashboard-https", daemon=True)
            admin_thread.start()
            servers.callback(admin_thread.join)
            servers.callback(admin_server.shutdown)
            logging.info("Dashboard lauscht über HTTPS auf Port %s", admin_server.server_port)
        poller.start()
        servers.callback(poller.close)
        logging.info("Dashboard lauscht auf Port %s", server.server_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
