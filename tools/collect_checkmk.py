#!/usr/bin/env python3
"""Write read-only status snapshots from a Checkmk Livestatus command."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path


MAX_BODY = 1024 * 1024


def livestatus(command, table, columns, *, filter_line=None):
    query = f"GET {table}\nColumns: {columns}\nOutputFormat: json\n"
    if filter_line:
        if not filter_line.isprintable():
            raise ValueError("Ungültiger Checkmk-Filter")
        query += f"Filter: {filter_line}\n"
    query += "\n"
    answer = subprocess.run(command, input=query, text=True, capture_output=True, check=True, timeout=8)
    if len(answer.stdout) > MAX_BODY:
        raise ValueError("Checkmk-Antwort zu groß")
    rows = json.loads(answer.stdout)
    if not isinstance(rows, list) or (not rows and filter_line is None):
        raise ValueError("Livestatus lieferte keine Zustände")
    return rows


def measurement(state, observed_at, metrics=(), detail=None, *, progress=None):
    data = {"state": state, "observed_at": observed_at,
            "metrics": [{"label": label, "value": value} for label, value in metrics]}
    if detail:
        data["detail"] = detail
    if progress is not None:
        data["progress"] = progress
    return data


def summarize(hosts, services, now):
    host_states = Counter(state if checked else -1 for _, state, checked, _ in hosts)
    service_states = Counter(state if checked else -1 for _, _, state, checked, _, _, _ in services)
    overdue = sum(checked and enabled and interval > 0 and now - stamp > max(180, 180 * interval)
                  for _, _, _, checked, enabled, interval, stamp in services)
    hosts_without_current_check = sum(not checked or not 0 <= now - stamp <= 180
                                      for _, _, checked, stamp in hosts)
    state = "ok"
    if host_states[1] or host_states[2] or service_states[1] or service_states[2] or service_states[3]:
        state = "warning"
    if overdue or hosts_without_current_check or service_states[-1]:
        state = "warning"
    return measurement(state, now, (
        ("Hosts UP", f"{host_states[0]} / {len(hosts)}"),
        ("Dienste OK", f"{service_states[0]} / {len(services)}"),
        ("Warnungen / kritisch", f"{service_states[1]} / {service_states[2]}"),
        ("Unbekannt / ausstehend", f"{service_states[3]} / {service_states[-1]}"),
    ), detail=(f"{overdue} überfällige Dienste, {hosts_without_current_check} Hosts ohne aktuellen Check"
               if overdue or hosts_without_current_check else None))


def dns_snapshot(rows, host, now):
    checks = [(state, stamp, perf) for name, state, checked, stamp, perf in rows if name == host and checked]
    if len(checks) != 1:
        return measurement("unknown", now)
    state, stamp, perf = checks[0]
    if not -10 <= now - stamp <= 180:
        return measurement("stale", stamp)
    status, value = {0: ("ok", "Antwortet"), 1: ("warning", "Warnung"),
                     2: ("down", "Fehlgeschlagen")}.get(state, ("unknown", "Unbekannt"))
    latency = re.search(r"(?:^| )time=([0-9]+(?:\.[0-9]+)?)s(?:;| |$)", perf)
    if status == "ok" and latency:
        value += f" · {float(latency[1]) * 1000:.0f} ms"
    return measurement(status, stamp, (("DNS-Auflösung", value),))


def write_snapshot(directory, name, data):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
            temporary.replace(directory / name)
        finally:
            temporary.unlink(missing_ok=True)


def snapshot_filename(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.json", value):
        raise argparse.ArgumentTypeError("use a lowercase snapshot filename ending in .json")
    return value


def command_arguments(parser, remainder):
    if remainder[:1] != ["--"] or len(remainder) < 2:
        parser.error("supply a Livestatus command after --")
    return remainder[1:]


def collect_snapshots(command, dns_checks):
    hosts = livestatus(command, "hosts", "name state has_been_checked last_check")
    services = livestatus(
        command, "services", "host_name description state has_been_checked active_checks_enabled check_interval last_check"
    )
    snapshots = {"checkmk.json": summarize(hosts, services, time.time())}
    for host, service, filename in dns_checks:
        rows = livestatus(
            command, "services", "host_name state has_been_checked last_check perf_data",
            filter_line=f"description = {service}",
        )
        snapshots[filename] = dns_snapshot(rows, host, time.time())
    return snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="existing snapshot directory")
    parser.add_argument("--print", action="store_true", dest="preview", help="print a read-only preview")
    parser.add_argument("--dns", action="append", nargs=3, default=[], metavar=("HOST", "SERVICE", "FILENAME"),
                        help="include a DNS check from the configured host and service")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.directory and not args.preview:
        parser.error("--directory is required unless --print is supplied")
    command = command_arguments(parser, args.command)
    try:
        filenames = [snapshot_filename(check[2]) for check in args.dns]
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if "checkmk.json" in filenames or len(filenames) != len(set(filenames)):
        parser.error("DNS checks need distinct filenames other than checkmk.json")
    try:
        snapshots = collect_snapshots(command, args.dns)
        if args.preview:
            print(json.dumps(snapshots, ensure_ascii=False, allow_nan=False))
        else:
            for name, snapshot in snapshots.items():
                write_snapshot(args.directory, name, snapshot)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Checkmk measurement unavailable ({type(error).__name__})\n")


if __name__ == "__main__":
    main()
