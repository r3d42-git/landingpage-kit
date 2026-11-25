"""Public video feeds for explicitly selected YouTube channels."""

from codecs import getincrementaldecoder
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import http.client
import re
from threading import BoundedSemaphore, Lock
import time
import unicodedata
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree


MAX_CHANNELS = 12
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOST = "www.youtube.com"
_HOSTS = {"youtube.com", _HOST, "m.youtube.com"}
_FEED_PATH = "/feeds/videos.xml"
_FEED_PATHS = {_FEED_PATH, "/xml/feeds/videos.xml"}
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_FEED_SIZE = 256 * 1024
_PAGE_SIZE = 4 * 1024 * 1024
_REQUEST_TIMEOUT = 3
_PROBE_TIMEOUT = 7
_CACHE_TTL = 15 * 60
_CACHE_SIZE = 128
_MAX_PENDING = 24
_PUBLIC_FEED_CAPACITY = 15
_INVALID_INPUT = "Bitte einen YouTube-Kanallink, eine Kanal-ID oder einen @Handle eingeben."
_NO_CHANNEL = "Der öffentliche YouTube-Kanal konnte nicht aufgelöst werden."

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="youtube-feed")
_handle_slots = BoundedSemaphore(2)
_lock = Lock()
_cache = OrderedDict()
_pending = {}


@dataclass(frozen=True)
class _Video:
    video_id: str
    title: str
    published: datetime


@dataclass(frozen=True)
class _Feed:
    name: str
    videos: tuple
    observed_at: float


def _display_text(value, limit):
    if not value:
        return ""
    return " ".join(value.split())[:limit].rstrip()


def _valid_handle(value):
    def alphanumeric(char):
        return unicodedata.category(char)[0] in "LN"

    return (
        1 <= len(value) <= 30
        and alphanumeric(value[0])
        and (alphanumeric(value[-1]) or unicodedata.category(value[-1])[0] == "M")
        and all(char in "_-.·" or unicodedata.category(char)[0] in "LNM" for char in value)
    )


def _channel_reference(value):
    if not isinstance(value, str):
        raise ValueError(_INVALID_INPUT)
    value = value.strip()
    if not value or len(value) > 500 or re.search(r"[\s\\\x00-\x1f\x7f#]", value):
        raise ValueError(_INVALID_INPUT)
    if CHANNEL_ID.fullmatch(value):
        return "id", value
    if value.startswith("@") and _valid_handle(value[1:]):
        return "handle", value[1:]
    try:
        parts = urlsplit(value if "://" in value else f"https://{value}")
        path = unquote(parts.path, errors="strict")
    except ValueError:
        raise ValueError(_INVALID_INPUT) from None
    if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in _HOSTS:
        raise ValueError(_INVALID_INPUT)
    if path in _FEED_PATHS:
        match = re.fullmatch(r"channel_id=(UC[A-Za-z0-9_-]{22})", parts.query)
        if match:
            return "id", match[1]
    elif not parts.query or re.fullmatch(r"si=[A-Za-z0-9_-]{1,128}", parts.query):
        match = re.fullmatch(r"/(channel/(UC[A-Za-z0-9_-]{22})|@([^/]+))(?:/(?:featured|videos|shorts|streams))?/?", path)
        if match and match[2]:
            return "id", match[2]
        if match and _valid_handle(match[3]):
            return "handle", match[3]
    raise ValueError(_INVALID_INPUT)


class _ChannelMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical_ids = set()
        self.feed_ids = set()

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        attributes = dict(attrs)
        canonical = attributes.get("rel") == "canonical"
        rss = attributes.get("type") == "application/rss+xml"
        href = attributes.get("href")
        if not href or not (canonical or rss):
            return
        try:
            path = urlsplit(href).path
            kind, value = _channel_reference(href)
        except ValueError:
            return
        if kind != "id":
            return
        if canonical and path.startswith("/channel/"):
            self.canonical_ids.add(value)
        if rss and path in _FEED_PATHS:
            self.feed_ids.add(value)


def _read_public(path, accept, limit, deadline, *, consume=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    connection = http.client.HTTPSConnection(_HOST, timeout=min(_REQUEST_TIMEOUT, remaining))
    try:
        connection.request("GET", path, headers={"Accept": accept})
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError(_NO_CHANNEL)
            body = bytearray()
            while time.monotonic() < deadline:
                chunk = response.read1(min(64 * 1024, limit + 1 - len(body)))
                if not chunk:
                    return bytes(body)
                body.extend(chunk)
                if len(body) > limit:
                    break
                if consume is not None and consume(chunk):
                    return b""
            raise ValueError(_NO_CHANNEL)
    finally:
        connection.close()


def _handle_channel_id(handle, deadline):
    metadata = _ChannelMetadata()
    decoder = getincrementaldecoder("utf-8")("replace")

    def consume(chunk):
        metadata.feed(decoder.decode(chunk))
        # YouTube also sends these links after the opening body tag.
        return bool(metadata.canonical_ids and metadata.feed_ids)

    if not _handle_slots.acquire(blocking=False):
        raise ValueError(_NO_CHANNEL)
    try:
        _read_public("/@" + quote(handle, safe=""), "text/html", _PAGE_SIZE, deadline, consume=consume)
    finally:
        _handle_slots.release()
    metadata.feed(decoder.decode(b"", final=True))
    metadata.close()
    channel_ids = metadata.canonical_ids | metadata.feed_ids
    if len(channel_ids) != 1:
        raise ValueError(_NO_CHANNEL)
    return channel_ids.pop()


def _parse_feed(payload, channel_id, observed_at):
    document = payload.decode("utf-8-sig")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", document, flags=re.IGNORECASE):
        raise ValueError(_NO_CHANNEL)
    root = ElementTree.fromstring(document)
    feed_id = root.findtext(f"{_YT}channelId")
    if root.tag != f"{_ATOM}feed" or feed_id not in {channel_id, channel_id[2:]}:
        raise ValueError(_NO_CHANNEL)
    name = _display_text(root.findtext(f"{_ATOM}title"), 120)
    if not name:
        raise ValueError(_NO_CHANNEL)
    videos = {}
    for entry in root.findall(f"{_ATOM}entry"):
        video_id = entry.findtext(f"{_YT}videoId", "")
        title = _display_text(entry.findtext(f"{_ATOM}title"), 160)
        published = datetime.fromisoformat(entry.findtext(f"{_ATOM}published", ""))
        if (
            entry.findtext(f"{_YT}channelId") != channel_id
            or not VIDEO_ID.fullmatch(video_id)
            or not title
            or published.utcoffset() is None
        ):
            raise ValueError(_NO_CHANNEL)
        video = _Video(video_id, title, published.astimezone(timezone.utc))
        if video_id not in videos or video.published > videos[video_id].published:
            videos[video_id] = video
    return _Feed(name, tuple(videos.values()), observed_at)


def _load_feed(channel_id, deadline):
    try:
        body = _read_public(
            f"{_FEED_PATH}?channel_id={channel_id}", "application/atom+xml", _FEED_SIZE, deadline,
        )
        feed = _parse_feed(body, channel_id, time.time())
        with _lock:
            _cache[channel_id] = time.monotonic() + _CACHE_TTL, feed
            _cache.move_to_end(channel_id)
            if len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
        return feed
    except (OSError, http.client.HTTPException, ElementTree.ParseError, ValueError, OverflowError):
        return None
    finally:
        with _lock:
            _pending.pop(channel_id, None)


def _feed_future(channel_id, deadline):
    with _lock:
        cached = _cache.get(channel_id)
        if cached is not None and cached[0] > time.monotonic():
            _cache.move_to_end(channel_id)
            future = Future()
            future.set_result(cached[1])
            return future
        if channel_id not in _pending and len(_pending) < _MAX_PENDING:
            _pending[channel_id] = _pool.submit(_load_feed, channel_id, deadline)
        return _pending.get(channel_id)


def resolve_channel(value: str) -> dict[str, str]:
    """Resolve a supported public channel reference into its stable ID and current name."""
    kind, identifier = _channel_reference(value)
    deadline = time.monotonic() + _PROBE_TIMEOUT
    try:
        channel_id = _handle_channel_id(identifier, deadline) if kind == "handle" else identifier
        future = _feed_future(channel_id, deadline)
        feed = future.result(timeout=max(0, deadline - time.monotonic())) if future is not None else None
    except (OSError, http.client.HTTPException, ValueError):
        raise ValueError(_NO_CHANNEL) from None
    if feed is None:
        raise ValueError(_NO_CHANNEL)
    return {"id": channel_id, "name": feed.name}


def _status(state, label, metrics=(), *, feeds=(), detail=None):
    result = {
        "state": state, "label": label,
        "metrics": [{"label": key, "value": value} for key, value in metrics],
    }
    if feeds:
        result["observed_at"] = min(feed.observed_at for feed in feeds)
        result["measurement_max_age"] = _CACHE_TTL + 15
    if detail:
        result["detail"] = detail
    return result


def probe_channels(channels: list[dict[str, str]]) -> dict:
    """Read each distinct feed and report a combined view of public releases."""
    if not isinstance(channels, list) or not 1 <= len(channels) <= MAX_CHANNELS:
        return _status("unknown", "Keine gültigen YouTube-Kanäle")
    ids = [channel.get("id") if isinstance(channel, dict) else None for channel in channels]
    if any(not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id) for channel_id in ids):
        return _status("unknown", "Keine gültigen YouTube-Kanäle")
    deadline = time.monotonic() + _PROBE_TIMEOUT
    unique_ids = list(dict.fromkeys(ids))
    futures = [_feed_future(channel_id, deadline) for channel_id in unique_ids]
    done, _ = wait([future for future in futures if future is not None], timeout=max(0, deadline - time.monotonic()))
    feeds = [feed for future in futures if future in done and (feed := future.result()) is not None]
    if not feeds:
        return _status("unknown", "YouTube-Feeds nicht verfügbar")
    if len(feeds) != len(unique_ids):
        return _status(
            "warning", "YouTube-Feeds teilweise verfügbar",
            [("Abrufbare Kanäle", f"{len(feeds)} von {len(unique_ids)}")], feeds=feeds,
            detail="Die Gesamtzahl und der neueste Upload lassen sich erst mit allen Kanälen bestimmen.",
        )
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    candidates = [(video, feed.name) for feed in feeds for video in feed.videos if video.published <= now]
    recent = sum(video.published >= cutoff for video, _ in candidates)
    capped = any(
        len(feed.videos) >= _PUBLIC_FEED_CAPACITY and min(video.published for video in feed.videos) >= cutoff
        for feed in feeds
    )
    count = recent
    if capped:
        count = f"Mindestens {recent}" if recent else "Unbekannt"
    metrics = [("Neue Videos (24 h)", count)]
    if not candidates:
        return _status("ok", "Keine veröffentlichten Videos in den Feeds", metrics, feeds=feeds)
    latest, channel_name = max(candidates, key=lambda candidate: candidate[0].published)
    metrics.extend([
        ("Neuester Upload", latest.title),
        ("Kanal", channel_name),
        ("Veröffentlicht", latest.published.strftime("%d.%m.%Y · %H:%M UTC")),
    ])
    return {**_status("ok", "Öffentliche YouTube-Feeds", metrics, feeds=feeds), "latest_video_id": latest.video_id}
