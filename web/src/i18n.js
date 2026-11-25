import {ref} from 'vue'

// English source messages double as translation keys. Only interface text is passed here.
const german = {
  'Dashboard': 'Startseite',
  'Unlock editing': 'Bearbeitung entsperren',
  'Settings': 'Einstellungen',
  'Edit service': 'Dienst bearbeiten',
  'Add service': 'Dienst hinzufügen',
  'Edit category': 'Kategorie bearbeiten',
  'Add category': 'Kategorie hinzufügen',
  'Move service': 'Dienst verschieben',
  'Restore backup': 'Sicherung wiederherstellen',
  'Discard draft?': 'Entwurf verwerfen?',
  'Load newer version?': 'Neuere Fassung laden?',
  'reachable': 'erreichbar',
  'with warnings': 'mit Hinweis',
  'down': 'ausgefallen',
  'stale': 'veraltet',
  'unknown': 'unbekannt',
  '{source} is unreachable': '{source} ist nicht erreichbar',
  '{source} reports a problem': '{source} meldet eine Störung',
  'Status of {source} is unclear': 'Status von {source} unklar',
  'Could not load the dashboard. {detail}': 'Startseite konnte nicht geladen werden. {detail}',
  'Could not retrieve status. {detail}': 'Statusabruf fehlgeschlagen. {detail}',
  'Could not load data sources. {detail}': 'Messquellen konnten nicht geladen werden. {detail}',
  'The HTTPS address for editing is invalid.': 'Die HTTPS-Adresse zum Bearbeiten ist ungültig.',
  'Incorrect password.': 'Das Passwort ist falsch.',
  'Too many sign-in attempts. Try again later.': 'Zu viele Anmeldeversuche. Bitte später erneut versuchen.',
  'Change undone.': 'Änderung rückgängig gemacht.',
  'Draft updated.': 'Entwurf aktualisiert.',
  'Category and its services removed.': 'Kategorie mit ihren Diensten entfernt.',
  'Service removed.': 'Dienst entfernt.',
  'Order changed.': 'Reihenfolge geändert.',
  'Dashboard saved.': 'Startseite gespeichert.',
  'The dashboard has changed. Your draft is still open.': 'Die Startseite wurde inzwischen geändert. Dein Entwurf bleibt geöffnet.',
  'Your session expired. Sign in to save your draft.': 'Die Sitzung ist abgelaufen. Nach der Anmeldung kannst du den Entwurf speichern.',
  'Latest version loaded.': 'Aktuelle Fassung geladen.',
  'Enter a title.': 'Bitte einen Titel eingeben.',
  'Settings applied to draft.': 'Einstellungen im Entwurf übernommen.',
  'Backup restored.': 'Sicherung wiederhergestellt.',
  'Signed out.': 'Abgemeldet.',
  'Skip to content': 'Zum Inhalt',
  'Status overview': 'Statusübersicht',
  'Clear status filter': 'Statusfilter aufheben',
  'Filter by this status': 'Nach diesem Status filtern',
  'Search services': 'Dienste suchen',
  'Search services …': 'Dienste suchen …',
  'Clear search': 'Suche leeren',
  'Switch to light theme': 'Helle Ansicht aktivieren',
  'Switch to dark theme': 'Dunkle Ansicht aktivieren',
  'Light theme': 'Helle Ansicht',
  'Dark theme': 'Dunkle Ansicht',
  'Edit': 'Bearbeiten',
  'Dashboard unavailable': 'Startseite nicht verfügbar',
  'Try again': 'Erneut versuchen',
  'Loading dashboard …': 'Startseite wird geladen …',
  'Check again': 'Erneut prüfen',
  'Infrastructure status': 'Infrastrukturstatus',
  'Only limited infrastructure status information is available.': 'Der Infrastrukturstatus lässt sich zurzeit nur eingeschränkt beurteilen.',
  'Checked {time}': 'Geprüft {time}',
  'Preview': 'Vorschau',
  'Editing': 'Bearbeitungsmodus',
  'Unsaved changes': 'Ungespeicherte Änderungen',
  'Draft': 'Entwurf',
  'Undo': 'Rückgängig',
  'Continue editing': 'Weiter bearbeiten',
  'Finish editing': 'Beenden',
  'Saving …': 'Speichert …',
  'Save': 'Speichern',
  'The saved version has changed.': 'Die gespeicherte Fassung hat sich geändert.',
  'Load latest version': 'Aktuelle Fassung laden',
  'Edit or move {name}': '{name} bearbeiten oder verschieben',
  'Edit or move category': 'Kategorie bearbeiten oder verschieben',
  'Add service to {name}': 'Dienst zu {name} hinzufügen',
  'Drop here to add at the end': 'Hier am Ende einfügen',
  'Add first service': 'Ersten Dienst hinzufügen',
  'No services yet.': 'Noch keine Dienste.',
  'Drop category at the end': 'Kategorie am Ende einfügen',
  'No matching services': 'Keine passenden Dienste',
  'Try a different search term.': 'Versuche einen anderen Suchbegriff.',
  'No services currently have this status.': 'Aktuell hat kein Dienst diesen Status.',
  'Show all services': 'Alle Dienste anzeigen',
  'Reset search': 'Suche zurücksetzen',
  'No categories yet': 'Noch keine Kategorien',
  'Enter edit mode to add your first category.': 'Im Bearbeitungsmodus kannst du die erste Kategorie anlegen.',
  'service': 'Dienst',
  'services': 'Dienste',
  'Category': 'Kategorie',
  'category': 'Kategorie',
  'categories': 'Kategorien',
  'Password': 'Passwort',
  'Cancel': 'Abbrechen',
  'Signing in …': 'Anmeldung läuft …',
  'Sign in': 'Anmelden',
  'Theme on this device': 'Darstellung auf diesem Gerät',
  'Use system setting': 'Wie Systemeinstellung',
  'Light': 'Hell',
  'Dark': 'Dunkel',
  'Title': 'Titel',
  'Subtitle': 'Untertitel',
  'Backup': 'Datensicherung',
  'The ZIP file contains the saved configuration and local logos.': 'Die ZIP-Datei enthält die gespeicherte Konfiguration und die lokalen Logos.',
  'Export': 'Exportieren',
  'Restore': 'Wiederherstellen',
  'Save or discard your draft before restoring a backup or signing out.': 'Speichere oder verwirf den Entwurf vor dem Wiederherstellen oder Abmelden.',
  'Sign out': 'Abmelden',
  'Sign in to edit': 'Zum Bearbeiten anmelden',
  'Apply': 'Übernehmen',
  'Done': 'Fertig',
  'Replace the saved dashboard and its logos with this backup?': 'Die gespeicherte Startseite und ihre Logos durch den Inhalt dieser Sicherung ersetzen?',
  'Discard your local draft and load the latest saved version?': 'Den lokalen Entwurf verwerfen und die aktuell gespeicherte Fassung laden?',
  'Your unsaved changes will be lost.': 'Deine ungespeicherten Änderungen gehen verloren.',
  'Load version': 'Fassung laden',
  'Discard': 'Verwerfen',
  'Could not identify the channel.': 'Der Kanal konnte nicht erkannt werden.',
  'This channel has already been added.': 'Dieser Kanal ist bereits hinzugefügt.',
  'Channel added.': 'Kanal hinzugefügt.',
  'The channel lookup timed out. Try again.': 'Die Kanalabfrage dauert zu lange. Bitte erneut versuchen.',
  'Channel removed.': 'Kanal entfernt.',
  'Enter a name.': 'Bitte einen Namen eingeben.',
  'Enter an HTTP(S) address or a local path starting with /.': 'Bitte eine HTTP(S)-Adresse oder einen lokalen Pfad mit / angeben.',
  'Select an available data source.': 'Bitte eine verfügbare Messquelle auswählen.',
  'Enter a complete HTTP(S) address for the availability check.': 'Für die Erreichbarkeitsprüfung bitte eine vollständige HTTP(S)-Adresse angeben.',
  'Add the channel you entered or clear the input.': 'Bitte den eingegebenen Kanal hinzufügen oder das Eingabefeld leeren.',
  'Enter the path to a local logo file under /assets/.': 'Bitte eine lokale Logo-Datei unter /assets/ angeben.',
  'Category name': 'Name der Kategorie',
  'Service name': 'Name des Dienstes',
  'Category icon': 'Kategoriesymbol',
  'Choosing an icon replaces the local logo.': 'Ein Symbol aus der Auswahl ersetzt das lokale Logo.',
  'Address': 'Adresse',
  'https:// or /local-path': 'https:// oder /lokaler-pfad',
  'Description': 'Beschreibung',
  'Monitoring': 'Überwachung',
  'Link only': 'Nur Verknüpfung',
  'HTTP availability': 'HTTP-Erreichbarkeit',
  'Service metrics': 'Dienstmetriken',
  'YouTube channels': 'YouTube-Kanäle',
  'Data source': 'Messquelle',
  'Select a source': 'Quelle auswählen',
  'Service address': 'Adresse des Dienstes',
  '{source} (currently unavailable)': '{source} (derzeit nicht verfügbar)',
  'Check availability': 'Erreichbarkeit prüfen',
  'For public HTTP(S) addresses and internal destinations approved for availability checks.': 'Für öffentliche HTTP(S)-Adressen und interne Ziele, die für Prüfungen freigegeben sind.',
  'No data source is currently available for this type.': 'Für diesen Typ ist aktuell keine Messquelle verfügbar.',
  'Remove {name}': '{name} entfernen',
  'Remove channel': 'Kanal entfernen',
  'Add a public channel to see new videos on this tile.': 'Füge einen öffentlichen Kanal hinzu, um neue Videos auf der Kachel zu sehen.',
  'Add channel': 'Kanal hinzufügen',
  '@handle, channel ID or URL': '@Handle, Kanal-ID oder URL',
  'Add': 'Hinzufügen',
  'Up to {count} channels per tile.': 'Maximal {count} Kanäle pro Kachel.',
  'Looking up channel …': 'Kanal wird gesucht …',
  'More options': 'Weitere Optionen',
  'Local logo': 'Lokales Logo',
  'Icon identifier': 'Symbolkennung',
  'Icon': 'Symbol',
  'e.g. server, shield or folder': 'z. B. server, shield oder folder',
  'Tag': 'Schlagwort',
  'Open link': 'Link öffnen',
  'In a new tab': 'In neuem Tab',
  'In this tab': 'In diesem Tab',
  'Before {name}': 'Vor {name}',
  'At the end': 'Am Ende',
  'New category': 'Neue Kategorie',
  'Delete category': 'Kategorie löschen',
  'Delete service': 'Dienst löschen',
  'Delete': 'Löschen',
  'Move': 'Verschieben',
  'Website reachable': 'Webseite erreichbar',
  'Warning': 'Warnung',
  'Unreachable from server': 'Vom Server nicht erreichbar',
  'Status out of date': 'Status veraltet',
  'Availability unknown': 'Erreichbarkeit unbekannt',
  'Not checked yet': 'Noch nicht geprüft',
  'No status yet': 'Noch kein Status',
  'Channels up to date': 'Kanäle aktuell',
  'Reachable': 'Erreichbar',
  'Channels unavailable': 'Kanäle nicht abrufbar',
  'Unreachable': 'Nicht erreichbar',
  'Stale': 'Veraltet',
  'Unknown': 'Unbekannt',
  'New service': 'Neuer Dienst',
  'Edit {name}': '{name} bearbeiten',
  'Last successful check': 'Zuletzt erfolgreich',
  'Last successful check: {time}': 'Zuletzt erfolgreich: {time}',
  'Checked or last successful: {time}': 'Geprüft bzw. zuletzt erfolgreich: {time}',
  'Last reading': 'Letzter Messwert',
  'More metrics': 'Weitere Messwerte',
  ' for {name}': ' für {name}',
  '{name} utilization: {percent}': 'Auslastung {name}: {percent}',
  'No YouTube channels configured.': 'Keine YouTube-Kanäle eingerichtet.',
  'Close': 'Schließen',
  'Queries (24 h)': 'Anfragen (24 h)',
  'Blocked (24 h)': 'Blockiert (24 h)',
  'DNS filter': 'DNS-Filter',
  'Network': 'Netzwerk',
  'Storage': 'Speicher',
  'Layers': 'Ebenen',
  'Folder': 'Ordner',
  'Player': 'Player',
  'Link': 'Link',
  'Server': 'Server',
  'Protection': 'Schutz',
  'Globe': 'Globus',
  'Home': 'Haus',
  'Tools': 'Werkzeug',
  'Display': 'Bildschirm',
  'just now': 'gerade eben',
  'on {date}': 'am {date}',
  'The server is currently unreachable.': 'Der Server ist zurzeit nicht erreichbar.',
  'Request failed (HTTP {status}).': 'Anfrage fehlgeschlagen (HTTP {status}).',
  'Language': 'Sprache',
  'Please fill in this field.': 'Bitte dieses Feld ausfüllen.',
}

const storageKey = 'startseite.language'
const supported = value => value === 'de' || value === 'en'

function initialLanguage() {
  // The read-only and editing ports have separate browser storage.
  const url = new URL(location.href)
  const transferred = url.searchParams.get('lang')
  if (url.searchParams.get('edit') === '1' && supported(transferred)) {
    try { localStorage.setItem(storageKey, transferred) } catch {}
    url.searchParams.delete('lang')
    history.replaceState(history.state, '', url)
    return transferred
  }
  try {
    const stored = localStorage.getItem(storageKey)
    if (supported(stored)) return stored
  } catch {}
  const preferred = navigator.languages?.length ? navigator.languages : [navigator.language]
  return preferred.map(value => value?.split('-')[0].toLowerCase()).find(supported) || 'de'
}

export const language = ref(initialLanguage())
document.documentElement.lang = language.value

export function setLanguage(value) {
  if (!supported(value)) return
  language.value = value
  document.documentElement.lang = value
  try { localStorage.setItem(storageKey, value) } catch {}
}

export function t(message, values = {}) {
  const text = language.value === 'de' ? german[message] ?? message : message
  return text.replace(/\{(\w+)\}/g, (_, key) => String(values[key]))
}

export const message = key => () => t(key)

export function localizeRequired(event) {
  const field = event.target
  if (field.validity.valueMissing) field.setCustomValidity(t('Please fill in this field.'))
}

export function resetValidation(event) {
  event.target.setCustomValidity?.('')
}
