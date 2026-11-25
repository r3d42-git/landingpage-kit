"""Schreibt die tatsächlich gemessene Dateisystembelegung als Snapshot."""

import argparse
import json
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
import time


def gibibytes(bytes_count):
    return f"{bytes_count / 1024**3:.1f}".replace(".", ",") + " GiB"


def snapshot(path):
    total, used, free = shutil.disk_usage(path)
    progress = round(100 * used / total, 1) if total else 0
    return {
        "observed_at": int(time.time()),
        "state": "warning" if progress >= 85 else "ok",
        "progress": progress,
        "metrics": [
            {"label": "Belegt", "value": gibibytes(used)},
            {"label": "Frei", "value": gibibytes(free)},
            {"label": "Kapazität", "value": gibibytes(total)},
            {"label": "Auslastung", "value": f"{progress:.1f}".replace(".", ",") + " %"},
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Ausgabedatei im Messverzeichnis")
    parser.add_argument("--path", type=Path, default=Path("/"), help="Pfad auf dem zu messenden Dateisystem")
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
