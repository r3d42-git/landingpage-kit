from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch

import public_youtube
from localization import render_reading


CHANNEL = "UCyyyyyyyyyyyyyyyyyyyyyy"
OTHER = "UC" + "x" * 22
YOUTUBE = "https://www.youtube.com"


def feed(channel_id=CHANNEL, name="Sample channel", videos=()):
    entries = "".join(
        f"""<entry><yt:videoId>{video_id}</yt:videoId><yt:channelId>{channel_id}</yt:channelId>
            <title>{title}</title><published>{published.isoformat()}</published>
            <updated>2099-10-01T07:00:00+00:00</updated></entry>"""
        for video_id, title, published in videos
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <yt:channelId>{channel_id[2:]}</yt:channelId><title>{name}</title>{entries}
    </feed>""".encode()


def channels(*ids):
    return [{"id": channel_id, "name": "Previously saved"} for channel_id in ids]


def metrics(result):
    return {item["label"]: item["value"] for item in result["metrics"]}


class PublicYouTubeTests(unittest.TestCase):
    def setUp(self):
        with public_youtube._lock:
            public_youtube._cache.clear()
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    def test_localizes_cached_reading_without_translating_channel_or_video_name(self):
        cached = {"state": "ok", "label": "Öffentliche YouTube-Feeds", "metrics": [
            {"label": "Neue Videos (24 h)", "value": "Mindestens 15"},
            {"label": "Neuester Upload", "value": "Mindestens 15"},
            {"label": "Kanal", "value": "Unbekannt"},
            {"label": "Veröffentlicht", "value": "25.11.2026 · 19:30 UTC"},
        ]}
        public_youtube.localize_legacy(cached)
        reading = render_reading(cached, "en")
        self.assertEqual(reading["label"], "Public YouTube feeds")
        self.assertEqual(reading["metrics"], [
            {"key": "new_videos_24h", "label": "New videos (24 h)", "value": "At least 15"},
            {"key": "latest_upload", "label": "Latest upload", "value": "Mindestens 15"},
            {"key": "channel", "label": "Channel", "value": "Unbekannt"},
            {"key": "published", "label": "Published", "value": "2026-11-25 · 19:30 UTC"},
        ])

    def test_resolves_canonical_public_references_to_a_shared_validated_feed(self):
        with patch.object(public_youtube, "_read_public", return_value=feed()) as request:
            for reference in (
                CHANNEL,
                f"youtube.com/channel/{CHANNEL}",
                f"{YOUTUBE}/channel/{CHANNEL}/videos?si=abcd-ef_1",
                f"{YOUTUBE}/feeds/videos.xml?channel_id={CHANNEL}",
                f"http://m.youtube.com/xml/feeds/videos.xml?channel_id={CHANNEL}",
            ):
                with self.subTest(reference=reference):
                    self.assertEqual(public_youtube.resolve_channel(reference),
                                     {"id": CHANNEL, "name": "Sample channel"})
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], f"/feeds/videos.xml?channel_id={CHANNEL}")

    def test_resolves_handle_using_metadata_in_streamed_channel_page(self):
        html = f"""<!doctype html><html><head>
        <script>const example = "https://attacker.invalid";</script>
        </head><body>
        <link rel="canonical" href="{YOUTUBE}/channel/{CHANNEL}">
        <link rel="alternate" type="application/rss+xml" href="{YOUTUBE}/feeds/videos.xml?channel_id={CHANNEL}">
        </body></html>""".encode()

        def request(path, _accept, _limit, _deadline, *, consume=None):
            if path == "/@ExampleChannel":
                self.assertFalse(consume(html[:52]))
                self.assertTrue(consume(html[52:]))
                return b""
            self.assertEqual(path, f"/feeds/videos.xml?channel_id={CHANNEL}")
            return feed()

        with patch.object(public_youtube, "_read_public", side_effect=request):
            self.assertEqual(public_youtube.resolve_channel("@ExampleChannel")["id"], CHANNEL)
            self.assertEqual(public_youtube.resolve_channel(f"{YOUTUBE}/@ExampleChannel/")["id"], CHANNEL)

    def test_accepts_percent_encoded_unicode_handle(self):
        def request(path, _accept, _limit, _deadline, *, consume=None):
            if consume:
                self.assertEqual(path, "/@%E6%95%99%E5%AD%A6")
                self.assertFalse(consume(f'<link rel="canonical" href="{YOUTUBE}/channel/{CHANNEL}"></head>'.encode()))
                return b""
            return feed()

        with patch.object(public_youtube, "_read_public", side_effect=request):
            self.assertEqual(public_youtube.resolve_channel(f"{YOUTUBE}/@%E6%95%99%E5%AD%A6")["id"], CHANNEL)

    def test_rejects_conflicting_handle_metadata(self):
        html = f"""<link rel="canonical" href="{YOUTUBE}/channel/{CHANNEL}">
        <link rel="alternate" type="application/rss+xml" href="{YOUTUBE}/feeds/videos.xml?channel_id={OTHER}">"""

        def request(_path, _accept, _limit, _deadline, *, consume):
            self.assertTrue(consume(html.encode()))
            return b""

        with patch.object(public_youtube, "_read_public", side_effect=request) as fetch:
            with self.assertRaisesRegex(ValueError, "nicht aufgelöst"):
                public_youtube.resolve_channel("@ExampleChannel")
        self.assertEqual(fetch.call_count, 1)

    def test_rejects_unapproved_or_ambiguous_references_before_network_access(self):
        values = (
            "https://attacker.invalid/@example",
            "https://www.youtube.com.attacker.invalid/@example",
            "https://www.youtube.com@attacker.invalid/@example",
            "https://attacker@www.youtube.com/@example",
            "https://www.youtube.com:443/@example",
            "https://www.youtube.com/@example%2F..%2Fredirect",
            "https://www.youtube.com/@example#fragment",
            "https://www.youtube.com/%FF",
            "https://www.youtube.com/@example?next=https://attacker.invalid",
            f"{YOUTUBE}/feeds/videos.xml?channel_id={CHANNEL}&channel_id={OTHER}",
            "@../../redirect",
        )
        with patch.object(public_youtube, "_read_public") as request:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    public_youtube.resolve_channel(value)
            request.assert_not_called()

    def test_redirect_is_not_followed(self):
        connection = MagicMock()
        response = connection.getresponse.return_value.__enter__.return_value
        response.status = 302
        response.getheader.return_value = "https://attacker.invalid/"
        with patch.object(public_youtube.http.client, "HTTPSConnection", return_value=connection) as connect:
            with self.assertRaisesRegex(ValueError, "nicht aufgelöst"):
                public_youtube.resolve_channel(CHANNEL)
        self.assertEqual(connect.call_args.args, ("www.youtube.com",))
        connection.request.assert_called_once_with(
            "GET", f"/feeds/videos.xml?channel_id={CHANNEL}", headers={"Accept": "application/atom+xml"},
        )
        connection.close.assert_called_once()

    def test_aggregates_unique_channels_and_uses_published_instead_of_updated(self):
        older, newer = self.now - timedelta(hours=5), self.now - timedelta(minutes=30)
        cutoff = self.now - timedelta(hours=25)
        upcoming = self.now + timedelta(hours=3)
        inputs = {
            CHANNEL: feed(videos=[
                ("yyyyyyyyyyy", "Older public release", older),
                ("oldoldoldol", "Earlier day", cutoff),
                ("yyyyyyyyyyy", "Repeated feed entry", older),
            ]),
            OTHER: feed(OTHER, "Second channel", [
                ("xxxxxxxxxxx", "Upcoming premiere", upcoming),
                ("newnewnewne", "Latest public release", newer),
            ]),
        }

        def request(path, *_args):
            return inputs[path.rsplit("=", 1)[1]]

        with patch.object(public_youtube, "_read_public", side_effect=request) as fetch:
            result = public_youtube.probe_channels(channels(CHANNEL, OTHER, CHANNEL))
            repeated = public_youtube.probe_channels(channels(CHANNEL, OTHER))
        self.assertEqual(result, repeated)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["latest_video_id"], "newnewnewne")
        self.assertEqual(metrics(result), {
            "Neue Videos (24 h)": 2, "Neuester Upload": "Latest public release", "Kanal": "Second channel",
            "Veröffentlicht": newer.strftime("%d.%m.%Y · %H:%M UTC"),
        })
        self.assertGreater(result["measurement_max_age"], 900)

    def test_distinguishes_empty_partial_and_failed_feeds(self):
        def request(path, *_args):
            return feed() if path.endswith(CHANNEL) else b"<html>Unavailable</html>"

        with patch.object(public_youtube, "_read_public", side_effect=request):
            empty = public_youtube.probe_channels(channels(CHANNEL))
            partial = public_youtube.probe_channels(channels(CHANNEL, OTHER))
            failed = public_youtube.probe_channels(channels(OTHER))
        self.assertEqual((empty["state"], metrics(empty)), ("ok", {"Neue Videos (24 h)": 0}))
        self.assertEqual((partial["state"], metrics(partial)), ("warning", {"Abrufbare Kanäle": "1 von 2"}))
        self.assertEqual((failed["state"], metrics(failed)), ("unknown", {}))
        for result in (empty, partial, failed):
            self.assertNotIn("latest_video_id", result)

    def test_refreshes_expired_feed(self):
        with patch.object(public_youtube, "_read_public", side_effect=[feed(), feed(name="Updated name")]) as fetch:
            self.assertEqual(public_youtube.resolve_channel(CHANNEL)["name"], "Sample channel")
            with public_youtube._lock:
                _expires_at, cached = public_youtube._cache[CHANNEL]
                public_youtube._cache[CHANNEL] = 0, cached
            self.assertEqual(public_youtube.resolve_channel(CHANNEL)["name"], "Updated name")
        self.assertEqual(fetch.call_count, 2)

    def test_excessive_card_channels_do_not_start_requests(self):
        with patch.object(public_youtube, "_read_public") as request:
            result = public_youtube.probe_channels(channels(*[CHANNEL] * 13))
            self.assertEqual((result["state"], metrics(result)), ("unknown", {}))
            request.assert_not_called()

    def test_feed_at_capacity_reports_a_lower_bound(self):
        videos = [(f"{index:011}", f"Video {index}", self.now - timedelta(hours=index)) for index in range(15)]
        with patch.object(public_youtube, "_read_public", return_value=feed(videos=videos)):
            result = public_youtube.probe_channels(channels(CHANNEL))
        self.assertEqual(metrics(result)["Neue Videos (24 h)"], "Mindestens 15")

    def test_rejects_unsafe_xml_and_mismatched_or_malformed_feed(self):
        payloads = (
            b'<!DOCTYPE feed [<!ENTITY untrusted "Expanded">]><feed>&untrusted;</feed>',
            feed(OTHER),
            feed(videos=[("no-id", "Invalid video", self.now)]),
            feed(videos=[("abcdefghijk", "Missing time zone", self.now.replace(tzinfo=None))]),
        )
        for response in payloads:
            with self.subTest(response=response), patch.object(public_youtube, "_read_public", return_value=response):
                self.assertEqual(public_youtube.probe_channels(channels(CHANNEL))["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
