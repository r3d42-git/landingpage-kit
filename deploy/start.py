"""Start the server using the files initialized in the persistent volume."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.cli_language import message


def main():
    password = os.environ.get("ADMIN_PASSWORD_FILE")
    if password and not Path(password).is_file():
        os.environ.pop("ADMIN_PASSWORD_FILE")

    cert = os.environ.get("TLS_CERT_FILE")
    key = os.environ.get("TLS_KEY_FILE")
    if cert and cert == key and not Path(cert).is_file():
        os.environ.pop("TLS_CERT_FILE")
        os.environ.pop("TLS_KEY_FILE")
    if not os.environ.get("TLS_CERT_FILE"):
        os.environ.pop("DASHBOARD_EDIT_URL", None)

    # Keep a configured password inactive until the HTTPS listener is available.
    if os.environ.get("ADMIN_PASSWORD_FILE") and not os.environ.get("TLS_CERT_FILE"):
        raise SystemExit(message("Für die Bearbeitung muss ein TLS-Zertifikat eingerichtet sein."))
    server = Path(__file__).resolve().parents[1] / "server.py"
    os.execv(sys.executable, [sys.executable, str(server)])


if __name__ == "__main__":
    main()
