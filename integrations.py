"""Read-only service probes selected by deployment configuration."""

import errno
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import public_youtube
from localization import Text, number, phrase, restore_text, translate


MAX_BODY = 1024 * 1024
TIMEOUT = 3
STATES = {"ok", "warning", "down", "unknown", "stale"}
DISPLAY_KINDS = {
    "http": "http",
    "tcp": "http",
    "host": "service",
    "pihole": "pihole",
    "prometheus": "service",
    "snmp_volume": "nas",
    "snapshot": "service",
}


_QUERIES = Text("Anfragen (24 h)", "Queries (24 h)", key="queries")
_BLOCKED = Text("Blockiert", "Blocked", key="blocked")
_CLIENTS = Text("Aktive Clients", "Active clients", key="active_clients")
_BLOCKLIST = Text("Domains auf Blocklisten", "Domains on blocklists", key="blocklist_domains")
_DNS_RESOLUTION = Text("DNS-Auflösung", "DNS resolution", key="dns_resolution")
_DNS_BLOCKING = Text("DNS-Blockierung", "DNS blocking", key="dns_blocking")
_STATS_API = Text("Statistik-API", "Statistics API", key="statistics_api")
_SCRAPE_TARGETS = Text("Scrape-Ziele aktiv", "Active scrape targets", key="active_scrape_targets")
_SCRAPE_TARGETS_DOWN = Text("Scrape-Ziele ausgefallen", "Down scrape targets", key="down_scrape_targets")
_USED = Text("Belegt", "Used", key="used")
_FREE = Text("Frei", "Free", key="free")
_CAPACITY = Text("Kapazität", "Capacity", key="capacity")
_USAGE = Text("Auslastung", "Usage", key="usage")
_RESPONSE = Text("Antwort", "Response", key="response")
_UNKNOWN = Text("Unbekannt", "Unknown")
_ACTIVE = Text("Aktiv", "Active")
_DISABLED = Text("Ausgeschaltet", "Disabled")
_MEASUREMENT_STALE = Text("Messung veraltet", "Measurement is stale")
_UNAVAILABLE = Text("Nicht verfügbar", "Unavailable")
_REACHABLE = Text("Erreichbar", "Reachable")
_REFUSED = Text("Verbindung abgelehnt", "Connection refused")
_NO_RESPONSE = Text("Keine Antwort", "No response")
_NO_ROUTE = Text("Keine Route", "No route")
_SNAPSHOT_STALE = Text("Letzte Messung ist veraltet", "The last measurement is stale")
_STORAGE_UNAVAILABLE = Text("Speichermessung über Prometheus nicht verfügbar", "Storage measurement from Prometheus unavailable")
_STORAGE_SNMP_FAILED = Text("SNMP-Messung über Prometheus fehlgeschlagen", "Prometheus SNMP measurement failed")
_STORAGE_INCOMPLETE = Text("Speichermessung unvollständig", "Storage measurement incomplete")
_STORAGE_STALE = Text("Speichermessung veraltet", "Storage measurement is stale")
_STORAGE_HIGH = Text("Auslastung bei mindestens 85 %", "Usage is at least 85%")
_STORAGE_WARNING = Text("Prometheus meldet eine Volume-Warnung", "Prometheus reports a volume warning")
_HTTP_OK = Text("Statusprüfung erfolgreich", "Status check succeeded")
_TCP_OK = Text("TCP-Verbindung erfolgreich", "TCP connection succeeded")
_HOST_OK = Text("Beide Dienste vom Server erreichbar", "Both services are reachable from the server")
_HOST_DOWN = Text("Vom Server aus antwortet keiner der beiden geprüften Ports", "Neither monitored port responds to the server")
_HOST_WARNING = Text("Mindestens eine Verbindung ist gestört", "At least one connection has a problem")
_HOST_UNKNOWN = Text("Host-Erreichbarkeit unklar", "Host reachability is unclear")
_NO_SERVICE_CHECK = Text("Für diesen Dienst ist keine Prüfung eingerichtet", "Monitoring is not configured for this service")
_NO_LINK_CHECK = Text("Für diesen Link ist keine Prüfung eingerichtet", "Monitoring is not configured for this link")
_TLS_FAILED = Text("TLS-Prüfung fehlgeschlagen", "TLS check failed")
_SERVICE_DOWN = Text("Dienst antwortet nicht", "Service is not responding")
_INCOMPLETE_CONFIGURATION = Text("Statusantwort oder Konfiguration ist unvollständig", "Status response or configuration is incomplete")
_SERVICE = Text("Dienst", "Service")
_WEB_SERVICE = Text("Webdienst", "Web service")


class ProbeError(Exception):
    def __init__(self, state, detail):
        super().__init__(detail)
        self.state = state
        self.detail = detail

    def localized(self, language):
        return translate(self.detail, language)


def _load_json(path):
    with path.open("rb") as stream:
        payload = stream.read(MAX_BODY + 1)
    if len(payload) > MAX_BODY:
        raise ValueError("Datei zu groß")
    return json.loads(payload)


def _configuration():
    path = os.environ.get("INTEGRATIONS_FILE", "/run/config/integrations.json")
    try:
        document = _load_json(Path(path))
        entries = {
            entry["id"]: entry
            for entry in document["sources"]
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", entry["id"])
            and isinstance(entry.get("label"), str)
            and entry.get("kind") in DISPLAY_KINDS
            and entry.get("display_kind", "service") in DISPLAY_KINDS.values()
        }
        endpoints = {_endpoint(value, origin_only=True)[0] for value in document.get("allowed_endpoints", [])}
        return entries, endpoints
    except (OSError, ValueError, AttributeError, KeyError, TypeError, ProbeError):
        return {}, set()


def sources():
    entries, _ = _configuration()
    return [
        {"id": entry["id"], "label": entry["label"],
         "kind": entry.get("display_kind", DISPLAY_KINDS[entry["kind"]])}
        for entry in entries.values()
    ]


def _endpoint(value, *, origin_only=False):
    # Reject ambiguous URLs before parsing. Requests are sent directly, without proxy settings.
    if not isinstance(value, str) or not value.isascii() or re.search(r"[\s\\\x00-\x1f\x7f#]", value):
        raise ProbeError("unknown", Text("Ungültiges Dienstziel", "Invalid service endpoint"))
    try:
        parts = urlsplit(value)
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
        host = parts.hostname
    except ValueError as error:
        raise ProbeError("unknown", Text("Ungültiges Dienstziel", "Invalid service endpoint")) from error
    if parts.scheme not in {"http", "https", "tcp"} or not host or not port or parts.port == 0:
        raise ProbeError("unknown", Text("Ungültiges Dienstziel", "Invalid service endpoint"))
    if parts.username is not None or parts.password is not None or not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise ProbeError("unknown", Text("Ungültiges Dienstziel", "Invalid service endpoint"))
    if (origin_only or parts.scheme == "tcp") and (parts.path not in {"", "/"} or parts.query):
        raise ProbeError("unknown", Text("Ungültiges Dienstziel", "Invalid service endpoint"))
    target = parts.path or "/"
    if target.startswith("//"):
        raise ProbeError("unknown", Text("Ungültiges Dienstziel", "Invalid service endpoint"))
    if parts.query:
        target += "?" + parts.query
    return (parts.scheme, host, port), target


def _connection(url, permitted, entry, *, tcp=False, timeout=TIMEOUT):
    origin, target = _endpoint(url)
    scheme, host, port = origin
    if origin not in permitted or tcp != (scheme == "tcp"):
        raise ProbeError("unknown", Text("Dienstziel ist nicht zur Prüfung freigegeben", "The service endpoint is not allowed for monitoring"))
    if tcp:
        return socket.create_connection((host, port), timeout=timeout), target
    pin = entry.get("tls_sha256")
    if pin is not None and (scheme != "https" or not isinstance(pin, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pin)):
        raise ProbeError("unknown", Text("Ungültiger TLS-Fingerabdruck", "Invalid TLS fingerprint"))
    if scheme == "https":
        if pin:
            # The deployment pins a specific self-signed peer. Verify it before sending HTTP.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context = ssl.create_default_context(cafile=entry.get("ca_file"))
        connection = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=context)
    else:
        connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    if pin:
        try:
            connection.connect()
            fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(fingerprint, pin.lower()):
                raise ProbeError("unknown", Text("TLS-Fingerabdruck stimmt nicht überein", "TLS fingerprint does not match"))
        except BaseException:
            connection.close()
            raise
    return connection, target


def _get(entry, permitted, *, url=None, statuses=(200,), read_json=False):
    connection, target = _connection(url or entry["url"], permitted, entry)
    try:
        connection.request("GET", target, headers={"Accept": "application/json" if read_json else "*/*"})
        with connection.getresponse() as response:
            if response.status not in statuses:
                state = "unknown" if response.status in {401, 403} else "down"
                raise ProbeError(state, phrase("Unerwartete Antwort (HTTP {})", "Unexpected response (HTTP {})", response.status))
            if not read_json:
                return response.status
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ProbeError("unknown", Text("Antwort überschreitet die zulässige Größe", "The response exceeds the size limit"))
            return json.loads(body)
    finally:
        connection.close()


def _result(state, label, metrics=(), *, observed_at=None, detail=None, progress=None):
    result = {"state": state, "label": label, "metrics": [
        {"label": key, "value": value} for key, value in metrics
    ]}
    if observed_at is not None:
        result["observed_at"] = observed_at
    if detail is not None:
        result["detail"] = detail
    if progress is not None:
        result["progress"] = progress
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Ungültiger Messwert")
    return value


def _integer(value):
    value = _number(value)
    if value < 0 or value != int(value):
        raise ValueError("Ungültiger Zähler")
    return int(value)


def _pihole_metrics(data):
    queries = data["queries"]
    total = _integer(queries["total"])
    blocked = _integer(queries["blocked"])
    percent = _number(queries["percent_blocked"])
    active = _integer(data["clients"]["active"])
    gravity = _integer(data["gravity"]["domains_being_blocked"])
    if blocked > total or not 0 <= percent <= 100:
        raise ValueError("Ungültige DNS-Zähler")
    return [
        (_QUERIES, number(total)),
        (_BLOCKED, phrase("{} · {} %", "{} · {}%", number(blocked), number(percent, 1))),
        (_CLIENTS, number(active)),
        (_BLOCKLIST, number(gravity)),
    ]


def _pihole_dns(entry):
    if not entry.get("dns_file"):
        return None
    try:
        return _snapshot({**entry, "file": entry["dns_file"]})
    except (ProbeError, OSError, ValueError, TypeError, KeyError):
        return {"state": "unknown", "metrics": []}


def _pihole(entry, permitted):
    dns = _pihole_dns(entry)
    dns_state = dns["state"] if dns else "ok"
    dns_metrics = [(item["label"], item["value"]) for item in dns["metrics"]] if dns else []
    if dns_state == "stale":
        dns_metrics = [(_DNS_RESOLUTION, _MEASUREMENT_STALE)]
    elif dns_state == "unknown":
        dns_metrics = [(_DNS_RESOLUTION, _UNKNOWN)]
    try:
        metrics = _pihole_metrics(_get(entry, permitted, read_json=True))
    except (ProbeError, OSError, http.client.HTTPException, ValueError, TypeError, KeyError, OverflowError):
        if dns is None:
            raise
        return _result("down" if dns_state == "down" else "warning", entry["label"],
                       (*dns_metrics, (_STATS_API, _UNAVAILABLE)),
                       observed_at=dns.get("observed_at"))
    blocking = None
    if entry.get("blocking_url"):
        if _endpoint(entry["blocking_url"])[0] != _endpoint(entry["url"])[0]:
            raise ProbeError("unknown", Text("DNS-Prüfungen müssen dieselbe Gegenstelle nutzen", "DNS checks must use the same endpoint"))
        try:
            blocking = _get(entry, permitted, url=entry["blocking_url"], read_json=True)["blocking"]
        except (ProbeError, OSError, http.client.HTTPException, ValueError, TypeError, KeyError):
            pass
        if blocking == "enabled":
            blocking = _ACTIVE
        elif blocking == "disabled":
            blocking = _DISABLED
        else:
            blocking = _UNKNOWN
        metrics.append((_DNS_BLOCKING, blocking))
    state = "down" if dns_state == "down" else "ok"
    if state == "ok" and (dns_state != "ok" or blocking in {_DISABLED, _UNKNOWN}):
        state = "warning"
    return _result(state, entry["label"], (*metrics, *dns_metrics), observed_at=time.time())


def _prometheus_url(entry, path):
    _endpoint(entry["url"], origin_only=True)
    return entry["url"].rstrip("/") + path


def _prometheus(entry, permitted):
    _get(entry, permitted, url=_prometheus_url(entry, "/-/ready"))
    data = _get(entry, permitted, url=_prometheus_url(entry, "/api/v1/targets?state=active"), read_json=True)
    targets = data["data"]["activeTargets"]
    if data["status"] != "success" or not isinstance(targets, list):
        raise ValueError("Ungültige Prometheus-Antwort")
    healthy = sum(target["health"] == "up" for target in targets)
    down = sum(target["health"] == "down" for target in targets)
    return _result("unknown" if not targets else "warning" if healthy < len(targets) else "ok", entry["label"], (
        (_SCRAPE_TARGETS, f"{healthy} / {len(targets)}"),
        (_SCRAPE_TARGETS_DOWN, number(down)),
    ), observed_at=time.time())


def _storage_query(entry):
    job = json.dumps(entry["job"], ensure_ascii=False)
    instance = json.dumps(entry["instance"], ensure_ascii=False)
    volume = json.dumps(entry["volume"], ensure_ascii=False)
    host_filter = f"job={job},instance={instance}"
    volume_filter = f"{host_filter},hrStorageDescr={volume}"
    selectors = {
        name: f"{name}{{{volume_filter}}}"
        for name in ("hrStorageSize", "hrStorageUsed", "hrStorageAllocationUnits")
    }
    storage_metrics = "|".join(selectors)
    storage_values = f'{{__name__=~"{storage_metrics}",{volume_filter}}}'
    selectors["up"] = f"up{{{host_filter}}}"
    raw_values = [storage_values, selectors["up"]]
    health_metric = entry.get("health_metric")
    if health_metric is not None:
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", health_metric) or health_metric in selectors:
            raise ValueError("Ungültige Prometheus-Metrik für die Volume-Gesundheit")
        selectors[health_metric] = health_metric
        raw_values.append(f'label_replace({health_metric},"source_value","{health_metric}","__name__",".*")')
    # Group the storage metrics so that PromQL `or` does not deduplicate their shared labels.
    query = " or ".join([*raw_values, *(
        f'label_replace(timestamp({selector}),"source_metric","{name}","__name__",".*")'
        for name, selector in selectors.items()
    )])
    return query, selectors.keys()


def _storage(entry, permitted):
    query, names = _storage_query(entry)
    url = _prometheus_url(entry, "/api/v1/query?" + urlencode({"query": query}))
    try:
        data = _get(entry, permitted, url=url, read_json=True)
    except (ProbeError, OSError, http.client.HTTPException, ValueError):
        return _result("stale", entry["label"], detail=_STORAGE_UNAVAILABLE)
    if data["status"] != "success" or data["data"]["resultType"] != "vector":
        raise ValueError("Ungültige Prometheus-Abfrage")
    values, samples = {}, {}
    for row in data["data"]["result"]:
        labels = row["metric"]
        key = labels.get("source_metric") or labels.get("source_value") or labels["__name__"]
        target = samples if "source_metric" in labels else values
        if key not in names or key in target:
            raise ValueError("Mehrdeutige Speichermessung")
        target[key] = _number(float(row["value"][1]))
    now = time.time()
    if "up" in samples and 0 <= now - samples["up"] <= 180 and values.get("up") == 0:
        return _result("stale", entry["label"], detail=_STORAGE_SNMP_FAILED)
    if set(names) != values.keys() or set(names) != samples.keys():
        return _result("stale", entry["label"], detail=_STORAGE_INCOMPLETE)
    oldest = min(samples.values())
    health_metric = entry.get("health_metric")
    if any(not -60 <= now - stamp <= (90 if key == health_metric else 180)
           for key, stamp in samples.items()):
        return _result("stale", entry["label"], observed_at=oldest, detail=_STORAGE_STALE)
    size, used, allocation = [_integer(values[key]) for key in ("hrStorageSize", "hrStorageUsed", "hrStorageAllocationUnits")]
    volume_healthy = values.get(health_metric, 1)
    if not size or used > size or not allocation or values["up"] != 1 or volume_healthy not in {0, 1}:
        raise ValueError("Ungültige Speichermessung")
    percent = 100 * used / size
    amounts = [(name, phrase("{} TB", "{} TB", number(blocks * allocation / 10**12, 2))) for name, blocks in (
        (_USED, used), (_FREE, size - used), (_CAPACITY, size),
    )]
    detail = None
    if percent >= 85:
        detail = _STORAGE_HIGH
    elif volume_healthy != 1:
        detail = _STORAGE_WARNING
    state = "warning" if detail else "ok"
    return _result(state, entry["label"], (*amounts, (_USAGE, phrase("{} %", "{}%", number(percent, 1)))),
                   observed_at=oldest, progress=round(percent, 1), detail=detail)


def _snapshot(entry):
    directory = Path(os.environ.get("INTEGRATIONS_METRICS_DIR", "/run/metrics"))
    name = entry["file"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.json", name):
        raise ProbeError("unknown", Text("Ungültiger Dateiname für Messwerte", "Invalid metrics filename"))
    try:
        data = _load_json(directory / name)
    except FileNotFoundError as error:
        raise ProbeError("unknown", Text("Messwerte noch nicht verfügbar", "Metrics are not available yet")) from error
    observed = _number(data["observed_at"])
    state = data["state"]
    if state not in STATES or observed > time.time() + 60:
        raise ValueError("Ungültige Messdatei")
    metrics = []
    for metric in data.get("metrics", []):
        label = restore_text(metric, "label", 100)
        value = restore_text(metric, "value", 160)
        if not isinstance(label, str):
            raise ValueError("Ungültige Messfeldbezeichnung")
        if not isinstance(value, str):
            _number(value)
        metrics.append((label, value))
    progress = data.get("progress")
    if progress is not None and not 0 <= _number(progress) <= 100:
        raise ValueError("Ungültige Auslastung")
    detail = restore_text(data, "detail", 500)
    if detail is not None and not isinstance(detail, str):
        raise ValueError("Ungültige Beschreibung")
    age = _number(entry.get("max_age", 180))
    if time.time() - observed > age:
        state, detail = "stale", _SNAPSHOT_STALE
    result = _result(state, entry["label"], metrics, observed_at=observed, detail=detail, progress=progress)
    if "max_age" in entry:
        result["measurement_max_age"] = age
    return result


def _http(entry, permitted):
    expected = entry.get("expect_json", {})
    required = entry.get("required_json_keys", [])
    metric_fields = entry.get("metric_fields", {})
    read_json = bool(expected or required or metric_fields)
    data = _get(entry, permitted, statuses=entry.get("statuses", (200, 204)),
                read_json=read_json)
    if read_json:
        if not isinstance(data, dict):
            raise ProbeError("unknown", Text("Ungültiges Format der Statusantwort", "Invalid status response format"))
        if any(data.get(key) != value for key, value in expected.items()):
            raise ProbeError("warning", Text("Der Dienst meldet einen unerwarteten Zustand", "The service reported an unexpected state"))
        if any(key not in data or data[key] is None for key in required):
            raise ProbeError("unknown", Text("Statusfelder fehlen in der Antwort", "Status fields are missing from the response"))
        metrics = [(label, data[key]) for key, label in metric_fields.items()
                   if isinstance(data.get(key), str) and re.fullmatch(r"[a-zA-Z0-9.+_-]{1,60}", data[key])]
        return _result("ok", entry["label"], metrics, observed_at=time.time(),
                       detail=entry.get("detail", _HTTP_OK))
    return _result("ok", entry["label"], ((_RESPONSE, f"HTTP {data}"),), observed_at=time.time(),
                   detail=entry.get("detail"))


def _link_endpoint(value):
    if not isinstance(value, str) or re.search(r"[\\\x00-\x1f\x7f]", value) or value != value.strip():
        raise ProbeError("unknown", Text("Ungültige Linkadresse", "Invalid link address"))
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not re.fullmatch(
        r"(?:\[[0-9A-Fa-f:.]+\]|[^\s:@%\[\]]+)(?::[0-9]+)?", parts.netloc
    ):
        raise ProbeError("unknown", Text("Ungültige Linkadresse", "Invalid link address"))
    host = parts.hostname.encode("idna").decode("ascii")
    authority = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        authority += f":{parts.port}"
    path = quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
    return _endpoint(parts._replace(netloc=authority, path=path, query=query, fragment="").geturl())


def _public_address(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        # Only native global unicast. Exclude transition and translation mechanisms.
        elif (address not in ipaddress.IPv6Network("2000::/3") or address.sixtofour
              or address.teredo):
            return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def _link_socket(host, port, allowed_origin):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not allowed_origin and any(not _public_address(addr[4][0]) for addr in addresses):
        raise ProbeError("unknown", Text("Internes Linkziel ist nicht zur Prüfung freigegeben", "The internal link target is not allowed for monitoring"))
    deadline = time.monotonic() + TIMEOUT
    failure = OSError("Kein Ziel erreichbar")
    for family, kind, protocol, _, sockaddr in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(remaining)
                sock.connect(sockaddr)
                return sock
            except BaseException:
                sock.close()
                raise
        except OSError as error:
            failure = error
    raise failure


def _link(entry, permitted):
    origin, target = _link_endpoint(entry.get("url"))
    scheme, host, port = origin
    allowed = origin in permitted
    if not allowed and port != (443 if scheme == "https" else 80):
        raise ProbeError("unknown", Text("Linkport ist nicht zur Prüfung freigegeben", "The link port is not allowed for monitoring"))
    # Pin the socket to the validated DNS result. Retain the URL host for TLS and HTTP.
    with _link_socket(host, port, allowed) as sock:
        if scheme == "https":
            context = ssl.create_default_context()
            connection = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=context)
            connection.sock = context.wrap_socket(sock, server_hostname=host)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
            connection.sock = sock
        try:
            connection.request("GET", target, headers={
                "Accept": "*/*",
                "User-Agent": "LandingpageKit/1.0 (website availability check)",
            })
            with connection.getresponse() as response:
                code = response.status
                redirect = (code in {300, 301, 302, 303, 307, 308}
                            and bool((response.getheader("Location") or "").strip()))
                state = "ok" if 200 <= code < 300 or redirect else "down"
                return _result(state, entry["label"], ((_RESPONSE, f"HTTP {code}"),),
                               observed_at=time.time())
        finally:
            connection.close()


def _tcp(entry, permitted):
    connection, _ = _connection(entry["url"], permitted, entry, tcp=True)
    with connection:
        return _result("ok", entry["label"], observed_at=time.time(), detail=_TCP_OK)


def _host(entry, permitted):
    targets = entry["targets"]
    if not isinstance(targets, list) or len(targets) != 2:
        raise ProbeError("unknown", Text("Zwei unabhängige Ziele für den Hostcheck erforderlich", "The host check requires two independent targets"))
    endpoints = [_endpoint(target["url"])[0] for target in targets]
    first, second = endpoints
    if (any(endpoint not in permitted or endpoint[0] != "tcp" for endpoint in endpoints)
            or first[1] != second[1] or first[2] == second[2]):
        raise ProbeError("unknown", Text("Ungültige Ziele für den Hostcheck", "Invalid host check targets"))
    outcomes = []
    metrics = []
    for target in targets:
        try:
            connection, _ = _connection(target["url"], permitted, entry, tcp=True, timeout=1.8)
            connection.close()
            outcome = _REACHABLE
        except ConnectionRefusedError:
            outcome = _REFUSED
        except TimeoutError:
            outcome = _NO_RESPONSE
        except OSError as error:
            if error.errno in {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN}:
                outcome = _NO_ROUTE
            else:
                outcome = _UNKNOWN
        outcomes.append(outcome)
        metrics.append((target["label"], outcome))
    if all(outcome == _REACHABLE for outcome in outcomes):
        state, detail = "ok", _HOST_OK
    elif all(outcome in {_NO_RESPONSE, _NO_ROUTE} for outcome in outcomes):
        state, detail = "down", _HOST_DOWN
    elif _REACHABLE in outcomes or _REFUSED in outcomes:
        state, detail = "warning", _HOST_WARNING
    else:
        state, detail = "unknown", _HOST_UNKNOWN
    return _result(state, entry["label"], metrics, observed_at=time.time(), detail=detail)


def _source_entry(item, entries):
    source = item.get("source") or item.get("id")
    entry = entries.get(source) if isinstance(source, str) else None
    if item.get("kind") == "link":
        if item.get("check_link") is not True:
            return None
        return {"kind": "link", "label": item.get("name") or "Link", "url": item.get("url")}
    if entry is None and item.get("kind") == "http" and not item.get("source"):
        return {"kind": "http", "label": item.get("name") or _WEB_SERVICE, "url": item.get("url")}
    return entry


def probe(item):
    if not isinstance(item, dict):
        return _result("unknown", _SERVICE, detail=_NO_SERVICE_CHECK)
    if item.get("kind") == "youtube":
        return public_youtube.probe_channels(item.get("channels", []))
    entries, permitted = _configuration()
    entry = _source_entry(item, entries)
    if entry is None:
        if item.get("kind") == "link":
            return _result("unknown", "Link", detail=_NO_LINK_CHECK)
        return _result("unknown", _SERVICE, detail=_NO_SERVICE_CHECK)
    try:
        kind = entry["kind"]
        if kind == "snapshot":
            return _snapshot(entry)
        providers = {"http": _http, "link": _link, "tcp": _tcp, "host": _host, "pihole": _pihole, "prometheus": _prometheus,
                     "snmp_volume": _storage}
        return providers[kind](entry, permitted)
    except ProbeError as error:
        return _result(error.state, entry["label"], detail=error.detail)
    except ssl.SSLError:
        return _result("down" if kind == "link" else "unknown", entry["label"], detail=_TLS_FAILED)
    except (OSError, http.client.HTTPException):
        return _result("down", entry["label"], detail=_SERVICE_DOWN)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, IndexError):
        return _result("unknown", entry["label"], detail=_INCOMPLETE_CONFIGURATION)


def _legacy_known(value, choices):
    return next((choice for choice in choices if choice == value), value)


_DE_INTEGER = r"(?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)"


def _legacy_metrics(metrics, labels):
    """Apply built-in labels and known numeric formats to matching cache entries."""
    for metric, label in zip(metrics, labels):
        if metric["label"] != label:
            continue
        metric["label"] = label
        value = metric["value"]
        if not isinstance(value, str):
            continue
        if label == _DNS_BLOCKING:
            metric["value"] = _legacy_known(value, (_ACTIVE, _DISABLED, _UNKNOWN))
        elif re.fullmatch(rf"{_DE_INTEGER}(?:,[0-9]+)?(?: (?:TB|%)| · {_DE_INTEGER},[0-9]+ %)?", value):
            english = value.translate(str.maketrans(",.", ".,")).replace(" %", "%")
            metric["value"] = Text(value, english)


def localize_legacy(item, reading):
    """Restore translations on cached probes with identifiable built-in fields."""
    entries, _ = _configuration()
    entry = _source_entry(item, entries)
    if entry is None:
        if reading.get("detail") == _NO_SERVICE_CHECK and reading["label"] == _SERVICE:
            reading.update(label=_SERVICE, detail=_NO_SERVICE_CHECK)
        return
    kind, metrics = entry["kind"], reading["metrics"]
    if isinstance(entry["label"], Text) and reading["label"] == entry["label"]:
        reading["label"] = entry["label"]
    if kind == "snapshot":
        return
    detail = reading.get("detail")
    if detail and detail != entry.get("detail"):
        choices = {
            "tcp": (_TCP_OK,),
            "http": (_HTTP_OK,),
            "host": (_HOST_OK, _HOST_DOWN, _HOST_WARNING, _HOST_UNKNOWN),
            "snmp_volume": (_STORAGE_UNAVAILABLE, _STORAGE_SNMP_FAILED, _STORAGE_INCOMPLETE,
                            _STORAGE_STALE, _STORAGE_HIGH, _STORAGE_WARNING),
        }
        reading["detail"] = _legacy_known(detail, (*choices.get(kind, ()), _TLS_FAILED, _SERVICE_DOWN, _INCOMPLETE_CONFIGURATION))
        if match := re.fullmatch(r"Unerwartete Antwort \(HTTP ([0-9]{3})\)", detail):
            reading["detail"] = phrase("Unerwartete Antwort (HTTP {})", "Unexpected response (HTTP {})", match[1])
    if kind == "host":
        for metric, target in zip(metrics, entry.get("targets", [])):
            if metric["label"] == target.get("label"):
                metric["value"] = _legacy_known(metric["value"], (_REACHABLE, _REFUSED, _NO_RESPONSE, _NO_ROUTE, _UNKNOWN))
    elif kind == "pihole":
        required = (_QUERIES, _BLOCKED, _CLIENTS, _BLOCKLIST)
        if [metric["label"] for metric in metrics[:4]] == list(required):
            _legacy_metrics(metrics, (*required, _DNS_BLOCKING) if entry.get("blocking_url") else required)
    elif kind == "prometheus":
        _legacy_metrics(metrics, (_SCRAPE_TARGETS, _SCRAPE_TARGETS_DOWN))
    elif kind == "snmp_volume":
        _legacy_metrics(metrics, (_USED, _FREE, _CAPACITY, _USAGE))
    elif kind == "link" or (kind == "http" and not (entry.get("expect_json") or entry.get("required_json_keys") or entry.get("metric_fields"))):
        _legacy_metrics(metrics, (_RESPONSE,))
