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
import public_youtube
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit


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


class ProbeError(Exception):
    def __init__(self, state, detail):
        super().__init__(detail)
        self.state = state


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
        raise ProbeError("unknown", "Ungültiges Dienstziel")
    try:
        parts = urlsplit(value)
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
        host = parts.hostname
    except ValueError as error:
        raise ProbeError("unknown", "Ungültiges Dienstziel") from error
    if parts.scheme not in {"http", "https", "tcp"} or not host or not port or parts.port == 0:
        raise ProbeError("unknown", "Ungültiges Dienstziel")
    if parts.username is not None or parts.password is not None or not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise ProbeError("unknown", "Ungültiges Dienstziel")
    if (origin_only or parts.scheme == "tcp") and (parts.path not in {"", "/"} or parts.query):
        raise ProbeError("unknown", "Ungültiges Dienstziel")
    target = parts.path or "/"
    if target.startswith("//"):
        raise ProbeError("unknown", "Ungültiges Dienstziel")
    if parts.query:
        target += "?" + parts.query
    return (parts.scheme, host, port), target


def _connection(url, permitted, entry, *, tcp=False, timeout=TIMEOUT):
    origin, target = _endpoint(url)
    scheme, host, port = origin
    if origin not in permitted or tcp != (scheme == "tcp"):
        raise ProbeError("unknown", "Dienstziel ist nicht zur Prüfung freigegeben")
    if tcp:
        return socket.create_connection((host, port), timeout=timeout), target
    pin = entry.get("tls_sha256")
    if pin is not None and (scheme != "https" or not isinstance(pin, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pin)):
        raise ProbeError("unknown", "Ungültiger TLS-Fingerabdruck")
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
                raise ProbeError("unknown", "TLS-Fingerabdruck stimmt nicht überein")
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
                raise ProbeError(state, f"Unerwartete Antwort (HTTP {response.status})")
            if not read_json:
                return response.status
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ProbeError("unknown", "Antwort überschreitet die zulässige Größe")
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


def _german(value, places=0):
    return f"{value:,.{places}f}".translate(str.maketrans(",.", ".,"))


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
        ("Anfragen (24 h)", _german(total)),
        ("Blockiert", f"{_german(blocked)} · {_german(percent, 1)} %"),
        ("Aktive Clients", _german(active)),
        ("Domains auf Blocklisten", _german(gravity)),
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
        dns_metrics = [("DNS-Auflösung", "Messung veraltet")]
    elif dns_state == "unknown":
        dns_metrics = [("DNS-Auflösung", "Unbekannt")]
    try:
        metrics = _pihole_metrics(_get(entry, permitted, read_json=True))
    except (ProbeError, OSError, http.client.HTTPException, ValueError, TypeError, KeyError, OverflowError):
        if dns is None:
            raise
        return _result("down" if dns_state == "down" else "warning", entry["label"],
                       (*dns_metrics, ("Statistik-API", "Nicht verfügbar")),
                       observed_at=dns.get("observed_at"))
    blocking = None
    if entry.get("blocking_url"):
        if _endpoint(entry["blocking_url"])[0] != _endpoint(entry["url"])[0]:
            raise ProbeError("unknown", "DNS-Prüfungen müssen dieselbe Gegenstelle nutzen")
        try:
            blocking = _get(entry, permitted, url=entry["blocking_url"], read_json=True)["blocking"]
        except (ProbeError, OSError, http.client.HTTPException, ValueError, TypeError, KeyError):
            pass
        if blocking == "enabled":
            blocking = "Aktiv"
        elif blocking == "disabled":
            blocking = "Ausgeschaltet"
        else:
            blocking = "Unbekannt"
        metrics.append(("DNS-Blockierung", blocking))
    state = "down" if dns_state == "down" else "ok"
    if state == "ok" and (dns_state != "ok" or blocking in {"Ausgeschaltet", "Unbekannt"}):
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
        ("Scrape-Ziele aktiv", f"{healthy} / {len(targets)}"),
        ("Scrape-Ziele ausgefallen", _german(down)),
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
        return _result("stale", entry["label"], detail="Speichermessung über Prometheus nicht verfügbar")
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
        return _result("stale", entry["label"], detail="SNMP-Messung über Prometheus fehlgeschlagen")
    if set(names) != values.keys() or set(names) != samples.keys():
        return _result("stale", entry["label"], detail="Speichermessung unvollständig")
    oldest = min(samples.values())
    health_metric = entry.get("health_metric")
    if any(not -60 <= now - stamp <= (90 if key == health_metric else 180)
           for key, stamp in samples.items()):
        return _result("stale", entry["label"], observed_at=oldest, detail="Speichermessung veraltet")
    size, used, allocation = [_integer(values[key]) for key in ("hrStorageSize", "hrStorageUsed", "hrStorageAllocationUnits")]
    volume_healthy = values.get(health_metric, 1)
    if not size or used > size or not allocation or values["up"] != 1 or volume_healthy not in {0, 1}:
        raise ValueError("Ungültige Speichermessung")
    percent = 100 * used / size
    amounts = [(name, f"{_german(blocks * allocation / 10**12, 2)} TB") for name, blocks in (
        ("Belegt", used), ("Frei", size - used), ("Kapazität", size),
    )]
    detail = None
    if percent >= 85:
        detail = "Auslastung bei mindestens 85 %"
    elif volume_healthy != 1:
        detail = "Prometheus meldet eine Volume-Warnung"
    state = "warning" if detail else "ok"
    return _result(state, entry["label"], (*amounts, ("Auslastung", f"{_german(percent, 1)} %")),
                   observed_at=oldest, progress=round(percent, 1), detail=detail)


def _snapshot(entry):
    directory = Path(os.environ.get("INTEGRATIONS_METRICS_DIR", "/run/metrics"))
    name = entry["file"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.json", name):
        raise ProbeError("unknown", "Ungültiger Dateiname für Messwerte")
    try:
        data = _load_json(directory / name)
    except FileNotFoundError as error:
        raise ProbeError("unknown", "Messwerte noch nicht verfügbar") from error
    observed = _number(data["observed_at"])
    state = data["state"]
    if state not in STATES or observed > time.time() + 60:
        raise ValueError("Ungültige Messdatei")
    metrics = []
    for metric in data.get("metrics", []):
        label, value = metric["label"], metric["value"]
        if not isinstance(label, str):
            raise ValueError("Ungültige Messfeldbezeichnung")
        if not isinstance(value, str):
            _number(value)
        metrics.append((label, value))
    progress = data.get("progress")
    if progress is not None and not 0 <= _number(progress) <= 100:
        raise ValueError("Ungültige Auslastung")
    detail = data.get("detail")
    if detail is not None and not isinstance(detail, str):
        raise ValueError("Ungültige Beschreibung")
    age = _number(entry.get("max_age", 180))
    if time.time() - observed > age:
        state, detail = "stale", "Letzte Messung ist veraltet"
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
            raise ProbeError("unknown", "Ungültiges Format der Statusantwort")
        if any(data.get(key) != value for key, value in expected.items()):
            raise ProbeError("warning", "Der Dienst meldet einen unerwarteten Zustand")
        if any(key not in data or data[key] is None for key in required):
            raise ProbeError("unknown", "Statusfelder fehlen in der Antwort")
        metrics = [(label, data[key]) for key, label in metric_fields.items()
                   if isinstance(data.get(key), str) and re.fullmatch(r"[a-zA-Z0-9.+_-]{1,60}", data[key])]
        return _result("ok", entry["label"], metrics, observed_at=time.time(),
                       detail=entry.get("detail", "Statusprüfung erfolgreich"))
    return _result("ok", entry["label"], (("Antwort", f"HTTP {data}"),), observed_at=time.time(),
                   detail=entry.get("detail"))


def _link_endpoint(value):
    if not isinstance(value, str) or re.search(r"[\\\x00-\x1f\x7f]", value) or value != value.strip():
        raise ProbeError("unknown", "Ungültige Linkadresse")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not re.fullmatch(
        r"(?:\[[0-9A-Fa-f:.]+\]|[^\s:@%\[\]]+)(?::[0-9]+)?", parts.netloc
    ):
        raise ProbeError("unknown", "Ungültige Linkadresse")
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
        raise ProbeError("unknown", "Internes Linkziel ist nicht zur Prüfung freigegeben")
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
        raise ProbeError("unknown", "Linkport ist nicht zur Prüfung freigegeben")
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
                return _result(state, entry["label"], (("Antwort", f"HTTP {code}"),),
                               observed_at=time.time())
        finally:
            connection.close()


def _tcp(entry, permitted):
    connection, _ = _connection(entry["url"], permitted, entry, tcp=True)
    with connection:
        return _result("ok", entry["label"], observed_at=time.time(), detail="TCP-Verbindung erfolgreich")


def _host(entry, permitted):
    targets = entry["targets"]
    if not isinstance(targets, list) or len(targets) != 2:
        raise ProbeError("unknown", "Zwei unabhängige Ziele für den Hostcheck erforderlich")
    endpoints = [_endpoint(target["url"])[0] for target in targets]
    first, second = endpoints
    if (any(endpoint not in permitted or endpoint[0] != "tcp" for endpoint in endpoints)
            or first[1] != second[1] or first[2] == second[2]):
        raise ProbeError("unknown", "Ungültige Ziele für den Hostcheck")
    outcomes = []
    metrics = []
    for target in targets:
        try:
            connection, _ = _connection(target["url"], permitted, entry, tcp=True, timeout=1.8)
            connection.close()
            outcome = "Erreichbar"
        except ConnectionRefusedError:
            outcome = "Verbindung abgelehnt"
        except TimeoutError:
            outcome = "Keine Antwort"
        except OSError as error:
            if error.errno in {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN}:
                outcome = "Keine Route"
            else:
                outcome = "Unbekannt"
        outcomes.append(outcome)
        metrics.append((target["label"], outcome))
    if all(outcome == "Erreichbar" for outcome in outcomes):
        state, detail = "ok", "Beide Dienste vom Server erreichbar"
    elif all(outcome in {"Keine Antwort", "Keine Route"} for outcome in outcomes):
        state, detail = "down", "Vom Server aus antwortet keiner der beiden geprüften Ports"
    elif "Erreichbar" in outcomes or "Verbindung abgelehnt" in outcomes:
        state, detail = "warning", "Mindestens eine Verbindung ist gestört"
    else:
        state, detail = "unknown", "Host-Erreichbarkeit unklar"
    return _result(state, entry["label"], metrics, observed_at=time.time(), detail=detail)


def probe(item):
    if not isinstance(item, dict):
        return _result("unknown", "Dienst", detail="Für diesen Dienst ist keine Prüfung eingerichtet")
    if item.get("kind") == "youtube":
        return public_youtube.probe_channels(item.get("channels", []))
    entries, permitted = _configuration()
    source = item.get("source") or item.get("id")
    entry = entries.get(source) if isinstance(source, str) else None
    if item.get("kind") == "link":
        if item.get("check_link") is not True:
            return _result("unknown", "Link", detail="Für diesen Link ist keine Prüfung eingerichtet")
        entry = {"kind": "link", "label": item.get("name") or "Link", "url": item.get("url")}
    elif entry is None and item.get("kind") == "http" and not item.get("source"):
        entry = {"kind": "http", "label": item.get("name") or "Webdienst", "url": item.get("url")}
    if entry is None:
        return _result("unknown", "Dienst", detail="Für diesen Dienst ist keine Prüfung eingerichtet")
    try:
        kind = entry["kind"]
        if kind == "snapshot":
            return _snapshot(entry)
        providers = {"http": _http, "link": _link, "tcp": _tcp, "host": _host, "pihole": _pihole, "prometheus": _prometheus,
                     "snmp_volume": _storage}
        return providers[kind](entry, permitted)
    except ProbeError as error:
        return _result(error.state, entry["label"], detail=str(error))
    except ssl.SSLError:
        return _result("down" if kind == "link" else "unknown", entry["label"], detail="TLS-Prüfung fehlgeschlagen")
    except (OSError, http.client.HTTPException):
        return _result("down", entry["label"], detail="Dienst antwortet nicht")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, IndexError):
        return _result("unknown", entry["label"], detail="Statusantwort oder Konfiguration ist unvollständig")
