"""Read PVE resource use from Prometheus and PBS health from Checkmk."""

import argparse
from dataclasses import dataclass
import http.client
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import time
from urllib.parse import urlencode, urlsplit


if __package__:
    from .collect_checkmk import command_arguments, livestatus, measurement as snapshot, snapshot_filename, write_snapshot
else:
    from collect_checkmk import command_arguments, livestatus, measurement as snapshot, snapshot_filename, write_snapshot


MAX_BODY = 1024 * 1024
PVE_MAX_AGE = 180
PVE_METRICS = (
    "up", "pve_up", "pve_guest_info", "pve_memory_usage_bytes", "pve_memory_size_bytes",
    "pve_disk_usage_bytes", "pve_disk_size_bytes",
)
CHECK_STATE = {0: "OK", 1: "Warnung", 2: "Kritisch", 3: "Unbekannt"}


@dataclass(frozen=True)
class Sample:
    value: float
    observed_at: float
    labels: dict


def finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Ungültiger Messwert")
    return number


def format_number(value, decimals=1):
    return f"{value:,.{decimals}f}".translate(str.maketrans(",.", ".,"))


def format_capacity(used, total, *, unit="GiB"):
    scale = 1024**3 if unit == "GiB" else 10**9
    return f"{format_number(used / scale)} / {format_number(total / scale)} {unit}"


def pve_query(job, instance):
    selectors = [f'job={json.dumps(job)}']
    if instance:
        selectors.append(f'instance={json.dumps(instance)}')
    labels = ",".join(selectors)
    label_filter = "{" + labels + "}"
    raw = '{__name__=~"' + "|".join(PVE_METRICS) + '",' + labels + "}"
    timestamps = [
        f'label_replace(timestamp({name}{label_filter}),"collector_metric","{name}","__name__",".*")'
        for name in PVE_METRICS
    ]
    return " or ".join([raw, *timestamps])


def fetch_pve(origin, job, instance):
    if not isinstance(origin, str) or re.search(r"[\s\\]", origin):
        raise ValueError("Ungültige Prometheus-Adresse")
    url = urlsplit(origin)
    if (
        url.scheme not in {"http", "https"} or not url.hostname or url.port == 0
        or url.username is not None or url.path not in {"", "/"} or url.query or url.fragment
    ):
        raise ValueError("Ungültige Prometheus-Adresse")
    connection_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(url.hostname, port=url.port, timeout=5)
    try:
        target = "/api/v1/query?" + urlencode({"query": pve_query(job, instance), "timeout": "3s"})
        connection.request("GET", target, headers={"Accept": "application/json"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError("Prometheus meldet einen HTTP-Fehler")
            data = response.read(MAX_BODY + 1)
    finally:
        connection.close()
    if len(data) > MAX_BODY:
        raise ValueError("Prometheus-Antwort zu groß")
    payload = json.loads(data)
    if payload["status"] != "success" or payload["data"]["resultType"] != "vector":
        raise ValueError("Ungültige Prometheus-Antwort")
    return payload["data"]["result"]


def pve_samples(rows):
    if not isinstance(rows, list):
        raise ValueError("Ungültige Prometheus-Antwort")
    samples, timestamps = {}, {}
    instances = set()
    for row in rows:
        labels = row["metric"]
        name = labels.get("collector_metric", labels.get("__name__"))
        key = (name, labels.get("id"))
        instances.add((labels["job"], labels["instance"]))
        targets = timestamps if "collector_metric" in labels else samples
        if name not in PVE_METRICS or key in targets:
            raise ValueError("Mehrdeutige PVE-Messwerte")
        targets[key] = (finite(row["value"][1]), labels)
    if len(instances) != 1:
        raise ValueError("Genau ein PVE-Scrape-Ziel muss ausgewählt sein")
    return {
        key: Sample(value, timestamps[key][0], labels)
        for key, (value, labels) in samples.items() if key in timestamps
    }


def fresh(sample, now, max_age=PVE_MAX_AGE):
    return sample is not None and -10 <= now - sample.observed_at <= max_age and sample.observed_at > 0


def append_capacity(metrics, observed, samples, nodes, kind, label, now):
    values = [samples.get((f"pve_{kind}_{measure}_bytes", node)) for node in nodes for measure in ("usage", "size")]
    if not all(fresh(sample, now) for sample in values):
        return
    used = sum(sample.value for sample in values[::2])
    total = sum(sample.value for sample in values[1::2])
    if not 0 <= used <= total or total <= 0:
        return
    metrics.append((label, format_capacity(used, total)))
    observed.extend(sample.observed_at for sample in values)


def pve_snapshot(rows, now):
    samples = pve_samples(rows)
    scrape = samples.get(("up", None))
    if scrape is None or scrape.value not in {0, 1} or not 0 < scrape.observed_at <= now + 10:
        raise ValueError("Aktueller PVE-Scrape-Status fehlt")
    if not fresh(scrape, now):
        return snapshot("stale", min(scrape.observed_at, now), detail="PVE-Exporter-Messung ist veraltet")
    if scrape.value == 0:
        return snapshot("down", scrape.observed_at, detail="Prometheus kann den PVE-Exporter nicht abfragen")
    nodes = {
        resource: sample for (name, resource), sample in samples.items()
        if name == "pve_up" and isinstance(resource, str) and resource.startswith("node/")
    }
    if not nodes or any(not fresh(sample, now) or sample.value not in {0, 1} for sample in nodes.values()):
        raise ValueError("Aktuelle PVE-Knotenmessungen fehlen")
    if all(sample.value == 0 for sample in nodes.values()):
        return snapshot("down", min(sample.observed_at for sample in nodes.values()), detail="Kein PVE-Knoten aktiv")
    statuses = {
        resource: sample for (name, resource), sample in samples.items()
        if name == "pve_up" and isinstance(resource, str) and resource.startswith(("lxc/", "qemu/"))
    }
    guests = {
        resource: sample for (name, resource), sample in samples.items()
        if name == "pve_guest_info" and isinstance(resource, str) and resource.startswith(("lxc/", "qemu/"))
    }
    if guests.keys() != statuses.keys() or any(
        not fresh(guest, now) or guest.value != 1 or not fresh(statuses[resource], now)
        or statuses[resource].value not in {0, 1} for resource, guest in guests.items()
    ):
        raise ValueError("PVE-Gastinventar ist unvollständig")
    observed = [scrape.observed_at, *(sample.observed_at for sample in nodes.values())]
    metrics = []
    for prefix, label in (("qemu/", "VMs aktiv"), ("lxc/", "LXC aktiv")):
        current = [resource for resource, guest in guests.items()
                   if resource.startswith(prefix) and guest.labels.get("template", "0") != "1"]
        active = sum(statuses[resource].value == 1 for resource in current)
        metrics.append((label, f"{active} / {len(current)}"))
        observed.extend(min(guests[resource].observed_at, statuses[resource].observed_at) for resource in current)
    append_capacity(metrics, observed, samples, nodes, "memory", "Host-RAM", now)
    append_capacity(metrics, observed, samples, nodes, "disk", "Host-Dateisystem", now)
    status = "warning" if any(sample.value == 0 for sample in nodes.values()) else "ok"
    return snapshot(status, min(observed), metrics, detail="Ein PVE-Knoten ist inaktiv" if status == "warning" else None)


def fetch_pbs(command, host):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", host):
        raise ValueError("Ungültiger Checkmk-Hostname")
    return livestatus(
        command, "services", "description state has_been_checked check_interval last_check perf_data",
        filter_line=f"host_name = {host}",
    )


def datastore_capacity(perf_data):
    metrics = dict(token.split("=", 1) for token in shlex.split(perf_data) if "=" in token)
    used, _, _, _, total = metrics["used_bytes"].split(";")
    if not used.endswith("B"):
        raise ValueError("Unerwartete Einheit für PBS-Datenspeicher")
    used, total = finite(used[:-1]), finite(total.removesuffix("B"))
    if not 0 <= used <= total or total <= 0:
        raise ValueError("Ungültige PBS-Kapazität")
    return used, total


def pbs_snapshot(rows, now, *, datastores_prefix, gc_service, https_service):
    if not isinstance(rows, list):
        raise ValueError("Ungültige Checkmk-Antwort")
    if not datastores_prefix or not gc_service or not https_service or gc_service == https_service or any(
        service.startswith(datastores_prefix) for service in (gc_service, https_service)
    ):
        raise ValueError("PBS-Prüfnamen fehlen oder überlappen")
    relevant = [
        (name, state, checked, finite(interval), finite(stamp), performance)
        for name, state, checked, interval, stamp, performance in rows
        if name in {gc_service, https_service} or name.startswith(datastores_prefix)
    ]
    if not relevant or len({row[0] for row in relevant}) != len(relevant):
        raise ValueError("PBS-Prüfungen fehlen oder sind mehrdeutig")
    current = [
        (name, state, stamp, performance)
        for name, state, checked, interval, stamp, performance in relevant
        if checked and state in CHECK_STATE and stamp > 0 and -10 <= now - stamp <= max(180, 180 * interval)
    ]
    last = max((stamp for _, _, checked, _, stamp, _ in relevant if checked and 0 < stamp <= now + 10), default=0)
    if not current:
        return snapshot("stale" if last else "unknown", min(last or now, now), detail="Keine frische PBS-Prüfung")
    metrics, percentages = [], []
    state = "ok"
    datastores = [row for row in current if row[0].startswith(datastores_prefix)]
    for name, result, _, performance in sorted(datastores):
        label = "Datenspeicher" if len(datastores) == 1 else f"Datenspeicher {name.removeprefix(datastores_prefix)}"[:100]
        try:
            used, total = datastore_capacity(performance)
            percent = 100 * used / total
            percentages.append(percent)
            capacity = f"{format_capacity(used, total, unit='GB')} · {format_number(percent)} %"
            value = capacity if result == 0 else f"{capacity} · {CHECK_STATE[result]}"
        except (KeyError, ValueError, TypeError):
            state, value = "warning", "Messwerte fehlen"
            if result in {1, 2}:
                value = f"{CHECK_STATE[result]} · {value}"
        metrics.append((label, value))
    checks = {name: result for name, result, _, _ in current}
    for name, label in ((gc_service, "Speicherbereinigung"), (https_service, "HTTPS")):
        if name in checks:
            value = "Erreichbar" if label == "HTTPS" and checks[name] == 0 else CHECK_STATE[checks[name]]
            metrics.append((label, value))
    complete = bool(datastores) and {https_service, gc_service} <= checks.keys()
    if len(current) != len(relevant) or not complete or any(value != 0 for value in checks.values()):
        state = "warning"
    if checks.get(https_service) == 2:
        state = "down"
    return snapshot(
        state, min(stamp for _, _, stamp, _ in current), metrics,
        detail="PBS-Prüfungen teilweise ausstehend oder veraltet" if len(current) != len(relevant) or not complete else None,
        progress=round(max(percentages), 1) if percentages else None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="platform", required=True)
    for platform in ("pve", "pbs"):
        subparser = commands.add_parser(platform)
        subparser.add_argument("--directory", type=Path, required=True, help="existing snapshot directory")
        subparser.add_argument("--file", type=snapshot_filename, default=f"{platform}.json", help="snapshot filename")
        if platform == "pve":
            subparser.add_argument("--origin", required=True, help="Prometheus origin")
            subparser.add_argument("--job", required=True, help="Proxmox exporter scrape job")
            subparser.add_argument("--instance", help="select one exporter within the scrape job")
        else:
            subparser.add_argument("--host", required=True, help="Checkmk hostname")
            subparser.add_argument("--datastore-prefix", required=True, help="prefix of datastore check names")
            subparser.add_argument("--gc-service", required=True, help="garbage collection check name")
            subparser.add_argument("--https-service", required=True, help="HTTPS check name")
            subparser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.platform == "pve":
            samples = fetch_pve(args.origin, args.job, args.instance)
            value = pve_snapshot(samples, time.time())
        else:
            command = command_arguments(parser, args.command)
            rows = fetch_pbs(command, args.host)
            value = pbs_snapshot(rows, time.time(), datastores_prefix=args.datastore_prefix,
                                 gc_service=args.gc_service, https_service=args.https_service)
        write_snapshot(args.directory, args.file, value)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError,
            http.client.HTTPException, subprocess.SubprocessError) as error:
        parser.exit(1, f"{args.platform.upper()} measurement unavailable ({type(error).__name__})\n")


if __name__ == "__main__":
    main()
