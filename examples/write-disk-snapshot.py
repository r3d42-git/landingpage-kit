"""Schreibt die tatsächlich gemessene Dateisystembelegung als Snapshot."""

import argparse
import json
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
import time


def metric(key, label, label_en, amount, unit):
    value = f"{amount:.1f}"
    english_unit = "%" if unit == "%" else f" {unit}"
    return {
        "key": key, "label": label, "label_en": label_en,
        "value": f"{value.replace('.', ',')} {unit}", "value_en": value + english_unit,
    }


def snapshot(path):
    total, used, free = shutil.disk_usage(path)
    progress = round(100 * used / total, 1) if total else 0
    return {
        "observed_at": int(time.time()),
        "state": "warning" if progress >= 85 else "ok",
        "progress": progress,
        "metrics": [
            metric("used", "Belegt", "Used", used / 1024**3, "GiB"),
            metric("free", "Frei", "Free", free / 1024**3, "GiB"),
            metric("capacity", "Kapazität", "Capacity", total / 1024**3, "GiB"),
            metric("usage", "Auslastung", "Usage", progress, "%"),
        ],
    }


def main():
    language = os.environ.get("DASHBOARD_LANGUAGE", "de").strip().lower().replace("_", "-")
    english = language.split("-")[0] == "en"
    parser = argparse.ArgumentParser(description=(
        "Write actual filesystem usage to a snapshot." if english else __doc__
    ))
    parser.add_argument("output", type=Path, help="output file in the metrics directory" if english else "Ausgabedatei im Messverzeichnis")
    parser.add_argument("--path", type=Path, default=Path("/"), help=(
        "path on the filesystem to measure" if english else "Pfad auf dem zu messenden Dateisystem"
    ))
    args = parser.parse_args()
    data = snapshot(args.path)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            json.dump(data, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fchmod(file.fileno(), 0o644)
            os.replace(pending, args.output)
        finally:
            pending.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except OSError as error:
        raise SystemExit(str(error)) from None
