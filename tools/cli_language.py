"""Language selection and messages for local administration commands."""

import os


ENGLISH = {
    "Dashboard-YAML einrichten und die lokale Administration konfigurieren.":
        "Set up the dashboard from YAML and configure local administration.",
    "YAML-Aliasse werden nicht unterstützt.": "YAML aliases are not supported.",
    "Schlüssel müssen eindeutig und Text sein.": "Keys must be unique strings.",
    "Die YAML-Datei überschreitet 1 MiB.": "The YAML file exceeds 1 MiB.",
    "Ungültiges YAML.": "Invalid YAML.",
    "Die YAML-Daten müssen sich als JSON darstellen lassen.": "The YAML data must be representable as JSON.",
    "Die Dashboard-Datei enthält ausschließlich settings und groups.":
        "The dashboard file may only contain settings and groups.",
    "Erwartet werden allowed_endpoints und sources.": "Expected allowed_endpoints and sources.",
    "allowed_endpoints und sources müssen Listen sein.": "allowed_endpoints and sources must be lists.",
    "Die ID ist bereits eingetragen.": "This ID is already in use.",
    "Ungültige Felder.": "Invalid fields.",
    "Die Messquellen sind zu groß.": "The list of data sources is too large.",
    "Eine Quelle muss als Zuordnung eingetragen werden.": "A source must be a mapping.",
    "Die ID darf Kleinbuchstaben, Ziffern und Bindestriche enthalten.":
        "The ID may contain lowercase letters, digits and hyphens.",
    "Unbekannte Art der Messquelle.": "Unknown source type.",
    "Ungültiger Kacheltyp für die Messquelle.": "Invalid tile type for this source.",
    "Eine Hostquelle benötigt zwei TCP-Ziele.": "A host source requires two TCP endpoints.",
    "Die Ziele benötigen denselben Host und unterschiedliche Ports.":
        "The endpoints must use the same host and different ports.",
    "TLS-Fingerabdrücke benötigen HTTPS und 64 Hexadezimalzeichen.":
        "TLS fingerprints require HTTPS and 64 hexadecimal characters.",
    "statuses benötigt eine Liste mit HTTP-Statuscodes von 100 bis 599.":
        "statuses must be a list of HTTP status codes between 100 and 599.",
    "required_json_keys benötigt eine Liste mit Feldnamen.":
        "required_json_keys must be a list of field names.",
    "expect_json und metric_fields benötigen Zuordnungen.": "expect_json and metric_fields must be mappings.",
    "DNS-Prüfungen müssen dieselbe Gegenstelle nutzen.": "DNS checks must use the same endpoint.",
    "Ungültige Prometheus-Metrik für die Volume-Gesundheit.": "Invalid Prometheus metric for volume health.",
    "Die Messdatei muss einen einfachen Namen mit der Endung .json haben.":
        "The snapshot file must have a plain filename ending in .json.",
    "max_age muss eine positive Anzahl Sekunden sein.": "max_age must be a positive number of seconds.",
    "Das Ziel ist mit seinem Protokoll und Port in allowed_endpoints einzutragen.":
        "Add the endpoint, including its protocol and port, to allowed_endpoints.",
    "--host benötigt einen DNS-Namen oder eine IP-Adresse.": "--host requires a DNS name or IP address.",
    "Für eine Eingabe ohne Terminal --stdin verwenden.": "Use --stdin when running without a terminal.",
    "Neues Admin-Passwort: ": "New admin password: ",
    "Admin-Passwort wiederholen: ": "Repeat admin password: ",
    "Die beiden Passworteingaben stimmen nicht überein.": "The passwords do not match.",
    "Mindestens 16 Zeichen und keine Leerzeichen am Anfang oder Ende verwenden.":
        "Use at least 16 characters and no leading or trailing whitespace.",
    "Das lokale CA-Zertifikat fehlt.": "The local CA certificate is missing.",
    "Dashboard-YAML ist gültig.": "Dashboard YAML is valid.",
    "SQLite ist bereits vorhanden. Änderungen über preview und apply übernehmen.":
        "The SQLite database already exists. Use preview and apply to update it.",
    "Startkonfiguration gespeichert. Beim ersten Start wird sie in SQLite übernommen.":
        "Initial configuration saved. The first startup will import it into SQLite.",
    "gespeichert": "saved",
    "vorgeschlagen": "proposed",
    "Die Revision hat sich geändert. Dashboard erneut mit preview prüfen.":
        "The revision has changed. Run preview again.",
    "Dashboard-YAML aus stdin prüfen": "Validate dashboard YAML from standard input",
    "Dashboard-YAML vor dem ersten Start einrichten": "Set up dashboard YAML before the first start",
    "Dashboard-YAML mit der laufenden Version vergleichen":
        "Compare dashboard YAML with the running instance",
    "Messquellen-YAML aus stdin prüfen": "Validate data source YAML from standard input",
    "Messquellen-YAML aus stdin speichern": "Save data source YAML from standard input",
    "Öffentliches lokales CA-Zertifikat ausgeben": "Export the public local CA certificate",
    "Dashboard-YAML in der überprüften Revision übernehmen":
        "Apply dashboard YAML to the revision you reviewed",
    "Admin-Passwort und beim ersten Mal lokales HTTPS einrichten":
        "Set the admin password and configure local HTTPS on first use",
    "Eine Passwortzeile aus stdin lesen": "Read one password line from standard input",
    "Lokales TLS-Zertifikat neu ausstellen": "Issue a new local TLS certificate",
    "Zusätzlicher DNS-Name oder zusätzliche IP-Adresse": "Additional DNS name or IP address",
    "Messquellen gespeichert.": "Data sources saved.",
    "Messquellen-YAML ist gültig.": "Data source YAML is valid.",
    "Admin-Passwort eingerichtet. Bei laufendem Dienst ist ein Neustart erforderlich.":
        "Admin password set. Restart the service if it is running.",
    "Lokales TLS-Zertifikat eingerichtet. Bei laufendem Dienst ist ein Neustart erforderlich.":
        "Local TLS certificate configured. Restart the service if it is running.",
    "Der laufende Dienst ist nicht erreichbar. Containerstatus und TLS-Konfiguration prüfen.":
        "The service is unreachable. Check the container status and TLS settings.",
    "OpenSSL konnte das lokale TLS-Zertifikat nicht erstellen.":
        "OpenSSL could not create the local TLS certificate.",
    "Kein Administrationskennwort konfiguriert.": "No admin password is configured.",
    "Kategorie nicht gefunden: ": "Category not found: ",
    "Eintrag nicht gefunden: ": "Tile not found: ",
    "Webseite alle fünf Minuten prüfen (Link-Kacheln)": "Check the website every five minutes (link tiles)",
    "Vollständiges Dashboard-JSON mit Revision aus stdin":
        "Read the full dashboard JSON and revision from standard input",
    "Vorhandene Sicherung aus stdin wiederherstellen": "Restore a backup from standard input",
    "ID der nachfolgenden Kachel": "ID of the tile to insert before",
    "ZIP-Ausgabe in eine Datei umleiten.": "Redirect ZIP output to a file.",
    "Sicherung zu groß.": "Backup is too large.",
    "Revision veraltet. Änderungen mit einem frischen Export abgleichen.":
        "The revision is out of date. Compare your changes with a fresh export.",
    "Name und URL sind erforderlich.": "Name and URL are required.",
    "Diese Kachel ist bereits ausgewählt.": "This tile is already selected.",
    "Die gewünschte Position gehört nicht zur Zielkategorie.":
        "The requested position does not belong to the target category.",
    "Gespeichert, Revision": "Saved, revision",
    "Für die Bearbeitung muss ein TLS-Zertifikat eingerichtet sein.":
        "Configure a TLS certificate to enable editing.",
    " Zeile {line}, Spalte {column}.": " Line {line}, column {column}.",
    "Messquelle {index}. {reason}": "Data source {index}. {reason}",
    "Aktuelle Revision: {revision}": "Current revision: {revision}",
    "Dashboard ist bereits aktuell.\n": "The dashboard is already up to date.\n",
    "Dashboard ist bereits aktuell. Revision {revision}.":
        "The dashboard is already up to date. Revision {revision}.",
    "Dashboard gespeichert. Revision {revision}.": "Dashboard saved. Revision {revision}.",
    "HTTP {code}. Vorschau, HTTPS-Einrichtung und passende Revision prüfen.":
        "HTTP {code}. Check the preview, HTTPS configuration and revision.",
}


def cli_language():
    setting = os.getenv("DASHBOARD_LANGUAGE", "de").strip().lower().replace("_", "-")
    return "en" if setting.split("-")[0] == "en" else "de"


def message(german, **values):
    template = ENGLISH[german] if cli_language() == "en" else german
    return template.format(**values) if values else template
