export const copy = value => JSON.parse(JSON.stringify(value))

export const categoryIcons = new Map([
  ['network','Netzwerk'], ['nas','Speicher'], ['layers','Ebenen'],
  ['folder','Ordner'], ['monitor-activity','Monitoring'], ['play-square','Player'],
  ['link','Link'], ['server','Server'], ['shield','Schutz'],
  ['globe','Globus'], ['home','Haus'], ['tools','Werkzeug'], ['monitor','Bildschirm']
])

const iconAliases = [
  [/monitor-activity/,'monitor-activity'],
  [/network|ethernet|router|netzwerk|sitemap/,'network'], [/shield|security|schutz|pihole|pi-hole/,'shield'],
  [/database|storage|hdd|speicher|nas/,'nas'], [/home|haus/,'home'], [/wrench|tools|werkzeug/,'tools'],
  [/server|infra|virtual|cube|docker/,'server'], [/globe|browser|www/,'globe'],
  [/monitor|desktop|chart|monitoring/,'monitor'], [/folder|ordner/,'folder']
]

export function entryIcon(entry, group = false) {
  if (group && categoryIcons.has(entry.icon)) return entry.icon
  const text = [entry.icon, entry.name].join(' ').toLowerCase()
  return iconAliases.find(([pattern]) => pattern.test(text))?.[1]
    || ({nas:'nas', pihole:'shield', service:'server', http:'globe', link:'link'})[entry.kind]
    || (group ? 'folder' : 'link')
}

const statusStates = ['ok', 'warning', 'down', 'stale', 'unknown']

export function serviceState(item, status, offline) {
  if (!item.kind || (item.kind === 'link' && item.check_link !== true)) return null
  if (item.kind === 'youtube' && !item.channels?.length) return null
  if (offline && status) return 'stale'
  return statusStates.includes(status?.state) ? status.state : 'unknown'
}

export function sameLinkCheck(first, second) {
  return (first.check_link === true) === (second.check_link === true)
    && (first.check_link !== true || first.url === second.url)
}

export function canCheckLink(raw) {
  if (!/^https?:\/\/[^/?#]/i.test(raw) || /[\s\\<>"`]/.test(raw)) return false
  try {
    const url = new URL(raw)
    return !!url.hostname && !url.username && !url.password
  }
  catch { return false }
}

export const youtubeChannelLimit = 12
export function sameYouTubeChannels(first = [], second = []) {
  return first.length === second.length && first.every((channel, index) => channel.id === second[index].id)
}

export function id() {
  if (globalThis.crypto.randomUUID) return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  return Array.from(bytes, (v, i) => ([4, 6, 8, 10].includes(i) ? '-' : '') + v.toString(16).padStart(2, '0')).join('')
}

export function safeLink(raw) {
  if (!raw) return ''
  try {
    const url = new URL(raw, location.origin)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch { return '' }
}
export function localLogo(raw) {
  if (!raw) return ''
  const url = safeLink(raw)
  if (!url) return ''
  const local = new URL(url)
  return local.origin === location.origin && local.pathname.startsWith('/assets/') ? local.pathname : ''
}
export function displayHost(raw) {
  if (!raw) return ''
  try {
    if (raw.startsWith('/')) return raw
    return new URL(raw).host
  } catch { return raw }
}
export const timeText = ts => ts ? new Date(ts * 1000).toLocaleString('de-DE', {dateStyle:'short', timeStyle:'short'}) : ''
export function ageText(ts, now) {
  if (!ts) return ''
  const minutes = Math.max(0, Math.floor((now / 1000 - ts) / 60))
  if (minutes < 1) return 'gerade eben'
  if (minutes < 60) return 'vor ' + minutes + ' Min.'
  if (minutes < 1440) return 'vor ' + Math.floor(minutes / 60) + ' Std.'
  return 'am ' + timeText(ts)
}
export function tone(text) {
  let hash = 0
  for (const char of String(text)) hash = (hash * 31 + char.codePointAt(0)) >>> 0
  return ['blue','violet','teal','amber','rose'][hash % 5]
}

export async function api(path, method = 'GET', data, signal) {
  const options = {method, credentials:'same-origin', cache:'no-store', headers:{}, signal}
  if (method !== 'GET') {
    options.headers['X-Dashboard-Request'] = '1'
    options.headers['Content-Type'] = data instanceof File ? 'application/zip' : 'application/json'
    options.body = data instanceof File ? data : JSON.stringify(data ?? {})
  }
  let response
  try { response = await fetch('/api/' + path, options) }
  catch { throw new Error('Der Server ist zurzeit nicht erreichbar.') }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const error = new Error(typeof body.error === 'string' ? body.error : 'Anfrage fehlgeschlagen (HTTP ' + response.status + ').')
    error.status = response.status
    throw error
  }
  if (path === 'backup') return response.blob()
  return response.status === 204 ? {} : response.json()
}
