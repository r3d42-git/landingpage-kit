import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import platform_metrics as platforms


NOW = 10_000
PBS_SERVICES = {
    "datastores_prefix": "Datastore ", "gc_service": "Maintenance", "https_service": "Web check",
}


def prometheus_rows(name, value, *, stamp=NOW - 40, resource=None, **labels):
    metric = {"job": "pve", "instance": "test", **labels}
    if resource is not None:
        metric["id"] = resource
    return [
        {"metric": {**metric, "__name__": name}, "value": [NOW, str(value)]},
        {"metric": {**metric, "collector_metric": name}, "value": [NOW, str(stamp)]},
    ]


class PlatformMetricsTest(unittest.TestCase):
    def test_pve_counts_running_guests_and_host_resources(self):
        gib = 1024**3
        rows = [
            *prometheus_rows("up", 1),
            *prometheus_rows("pve_up", 1, resource="node/test"),
            *prometheus_rows("pve_up", 1, resource="lxc/100"),
            *prometheus_rows("pve_guest_info", 1, resource="lxc/100", template="0", stamp=NOW - 50),
            *prometheus_rows("pve_up", 0, resource="lxc/101"),
            *prometheus_rows("pve_guest_info", 1, resource="lxc/101", template="0"),
            *prometheus_rows("pve_up", 1, resource="qemu/300"),
            *prometheus_rows("pve_guest_info", 1, resource="qemu/300", template="0"),
            *prometheus_rows("pve_up", 0, resource="qemu/301"),
            *prometheus_rows("pve_guest_info", 1, resource="qemu/301", template="1"),
            *prometheus_rows("pve_memory_usage_bytes", 4 * gib, resource="node/test"),
            *prometheus_rows("pve_memory_size_bytes", 32 * gib, resource="node/test"),
            *prometheus_rows("pve_disk_usage_bytes", 25 * gib, resource="node/test"),
            *prometheus_rows("pve_disk_size_bytes", 250 * gib, resource="node/test"),
        ]
        value = platforms.pve_snapshot(rows, NOW)
        self.assertEqual((value["state"], value["observed_at"]), ("ok", NOW - 50))
        self.assertEqual(value["metrics"], [
            {"label": "VMs aktiv", "value": "1 / 1"},
            {"label": "LXC aktiv", "value": "1 / 2"},
            {"label": "Host-RAM", "value": "4,0 / 32,0 GiB"},
            {"label": "Host-Dateisystem", "value": "25,0 / 250,0 GiB"},
        ])

    def test_pve_distinguishes_current_failure_and_old_data(self):
        down = platforms.pve_snapshot(prometheus_rows("up", 0), NOW)
        self.assertEqual((down["state"], down["observed_at"]), ("down", NOW - 40))
        stale = platforms.pve_snapshot(prometheus_rows("up", 1, stamp=NOW - 210), NOW)
        self.assertEqual((stale["state"], stale["observed_at"]), ("stale", NOW - 210))
        with self.assertRaises(ValueError):
            platforms.pve_snapshot([
                *prometheus_rows("up", 1),
                *prometheus_rows("pve_up", 1, resource="node/test"),
                *prometheus_rows("pve_up", 1, resource="lxc/100"),
            ], NOW)

    def test_pbs_uses_check_times_and_real_datastore_metrics(self):
        services = [
            ["Datastore sample", 0, 1, 1, NOW - 20, "used_bytes=20000000000B;;;0;400000000000"],
            ["Maintenance", 0, 1, 1, NOW - 35, ""],
            ["Web check", 0, 1, 1, NOW - 15, ""],
        ]
        value = platforms.pbs_snapshot(services, NOW, **PBS_SERVICES)
        self.assertEqual((value["state"], value["observed_at"], value["progress"]), ("ok", NOW - 35, 5.0))
        self.assertEqual(value["metrics"], [
            {"label": "Datenspeicher", "value": "20,0 / 400,0 GB · 5,0 %"},
            {"label": "Speicherbereinigung", "value": "OK"},
            {"label": "HTTPS", "value": "Erreichbar"},
        ])
        services[0][4] = NOW - 400
        partial = platforms.pbs_snapshot(services, NOW, **PBS_SERVICES)
        self.assertEqual(partial["state"], "warning")
        self.assertNotIn("progress", partial)
        self.assertEqual(partial["observed_at"], NOW - 35)
        services[2][1] = 2
        self.assertEqual(platforms.pbs_snapshot(services, NOW, **PBS_SERVICES)["state"], "down")
        self.assertEqual(platforms.pbs_snapshot(services, NOW + 180, **PBS_SERVICES)["state"], "stale")

    def test_invalid_datastore_is_not_zero_and_does_not_hide_others(self):
        value = platforms.pbs_snapshot([
            ["Datastore sample", 2, 1, 1, NOW, "used_bytes=NaNB;;;0;400000000000"],
            ["Maintenance", 0, 1, 1, NOW, ""],
            ["Web check", 0, 1, 1, NOW, ""],
        ], NOW, **PBS_SERVICES)
        self.assertEqual(value["state"], "warning")
        self.assertNotIn("progress", value)
        self.assertEqual(value["metrics"][0]["value"], "Kritisch · Messwerte fehlen")
        self.assertEqual(value["metrics"][-1]["value"], "Erreichbar")

    def test_pbs_requires_distinct_configured_service_names(self):
        with self.assertRaises(ValueError):
            platforms.pbs_snapshot([], NOW, datastores_prefix="Volume", gc_service="Volume cleanup",
                                   https_service="HTTPS")

    def test_collector_failure_preserves_last_snapshot(self):
        previous = platforms.snapshot("ok", NOW - 60, (("HTTPS", "Erreichbar"),))
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            platforms.write_snapshot(directory, "pbs.json", previous)
            result = subprocess.run([
                sys.executable, str(Path(platforms.__file__)), "pbs", "--directory", str(directory),
                "--host", "example", "--datastore-prefix", "Datastore ", "--gc-service", "Maintenance",
                "--https-service", "Web check", "--", sys.executable, "-c", "raise SystemExit(1)",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("measurement unavailable", result.stderr)
            self.assertEqual(json.loads((directory / "pbs.json").read_bytes()), previous)


if __name__ == "__main__":
    unittest.main()
