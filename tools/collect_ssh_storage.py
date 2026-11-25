#!/usr/bin/env python3
"""Write a read-only storage snapshot using a restricted SSH key."""

import argparse
import ipaddress
import json
import re
import subprocess
import time
from pathlib import Path

if __package__:
    from .collect_checkmk import measurement, snapshot_filename, write_snapshot
else:
    from collect_checkmk import measurement, snapshot_filename, write_snapshot

from localization import Text, number, phrase


SSH_TIMEOUT = 5


def ssh_command(config):
    if not isinstance(config, dict):
        raise ValueError("Ungültige Speicherkonfiguration")
    host = config["host"]
    if not isinstance(host, str):
        raise ValueError("Ungültiger SSH-Host")
    if ":" in host:
        if "%" in host:
            raise ValueError("Ungültiger SSH-Host")
        ipaddress.IPv6Address(host)
    elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ValueError("Ungültiger SSH-Host")
    port = config["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Ungültiger SSH-Port")
    user = config["user"]
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
        raise ValueError("Ungültiger SSH-Benutzer")
    for key in ("identity_file", "known_hosts_file"):
        path = config[key]
        if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in Path(path).parts:
            raise ValueError("Ungültiger SSH-Dateipfad")
    mountpoint = config["mountpoint"]
    if not isinstance(mountpoint, str) or (mountpoint != "/" and not re.fullmatch(
        r"(/[A-Za-z0-9_][A-Za-z0-9_.-]*)+", mountpoint
    )):
        raise ValueError("Ungültiger Einhängepunkt")
    return (
        "/usr/bin/ssh", "-F", "/dev/null", "-T",
        "-o", f'UserKnownHostsFile={config["known_hosts_file"]}',
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "ConnectionAttempts=1",
        "-i", config["identity_file"], "-p", str(port), "-l", user, "--", host, "df",
    )


def disk_usage(output, mountpoint):
    lines = output.splitlines()
    if len(lines) != 2:
        raise ValueError("df muss genau ein Dateisystem melden")
    header = lines[0].split()
    if (len(header) != 7 or header[:4] != ["Filesystem", "1024-blocks", "Used", "Available"]
            or header[4] not in {"Capacity", "Use%"} or header[5:] != ["Mounted", "on"]):
        raise ValueError("Ungültiger df-Kopf")
    row = re.fullmatch(r"\s*\S+\s+([0-9]{1,20})\s+([0-9]{1,20})\s+([0-9]{1,20})\s+[0-9]{1,3}%\s+(\S+)\s*", lines[1])
    if not row or row[4] != mountpoint:
        raise ValueError("df meldet nicht den erwarteten Einhängepunkt")
    total, used, available = map(int, row.groups()[:3])
    if not total or used + available > total:
        raise ValueError("Ungültige df-Speicherwerte")
    return total, used, available


def storage_snapshot(config_path):
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        command = ssh_command(config)
    except (OSError, ValueError, KeyError):
        return measurement("unknown", time.time(), detail=Text("Speicherkonfiguration ungültig", "Invalid storage configuration"))
    try:
        output = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                check=True, timeout=SSH_TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return measurement("unknown", time.time(), detail=Text("Speichermessung über SSH nicht verfügbar", "Storage measurement over SSH unavailable"))
    try:
        total, used, available = disk_usage(output.decode("ascii"), config["mountpoint"])
    except ValueError:
        return measurement("unknown", time.time(), detail=Text("Speichermessung lieferte ungültige Daten", "Storage measurement returned invalid data"))
    percent = used * 100 / total
    metrics = tuple((label, phrase("{} TB", "{} TB", number(blocks * 1024 / 10**12, 2, grouping=False)))
                    for label, blocks in ((Text("Belegt", "Used", key="used"), used),
                                          (Text("Frei", "Free", key="free"), available),
                                          (Text("Kapazität", "Capacity", key="capacity"), total)))
    metrics += ((Text("Auslastung", "Usage", key="usage"), phrase("{} %", "{}%", number(percent, 1))),)
    warning = used * 100 >= total * 85
    return measurement("warning" if warning else "ok", time.time(), metrics,
                       detail=Text("Auslastung bei mindestens 85 %", "Usage is at least 85%") if warning else None,
                       progress=round(percent, 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="private JSON configuration")
    parser.add_argument("--directory", type=Path, required=True, help="existing snapshot directory")
    parser.add_argument("--file", required=True, type=snapshot_filename, help="snapshot filename")
    args = parser.parse_args()
    snapshot = storage_snapshot(args.config)
    if snapshot["state"] == "unknown":
        parser.exit(1, snapshot["detail"] + "\n")
    write_snapshot(args.directory, args.file, snapshot)


if __name__ == "__main__":
    main()
