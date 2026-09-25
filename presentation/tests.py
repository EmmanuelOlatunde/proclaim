"""
Tests for the local presentation system.

- Scripture reference parsing / slide building (offline KJV)
- Song import parsing (plain text and ChordPro)
- Hymnal import: title/section building, idempotency, byte round-trip
- WebSocket protocol: present -> slide_change (minimal) -> blank (minimal)
"""
import json
import os
import shutil
import tempfile

import django.test
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from churchcast.asgi import application
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from .consumer import _split_lyrics
from .scripture import (
    parse_reference,
    build_slides,
    chapter_slides,
    chapter_verse_count,
    build_index,
    adjacent_chapter,
    canonicalize,
)
from .song_import import parse_import, clean_hymn_title, split_hymn_sections
from .management.commands.import_hymnals import import_hymnal_files
from .models import Song, SongSection, Room, ImageItem

ROOM_CODE = "WS1"


class ScriptureParserTests(django.test.SimpleTestCase):
    def test_single_verse(self):
        ref = parse_reference("John 3:16")
        self.assertIsNotNone(ref)
        self.assertEqual(ref["book"], "John")
        self.assertEqual(ref["chapter"], 3)
        self.assertEqual(ref["verse_start"], 16)
        self.assertEqual(ref["display"], "John 3:16")

    def test_verse_range(self):
        ref = parse_reference("Genesis 1:1-5")
        self.assertEqual(ref["book"], "Genesis")
        self.assertEqual(ref["verse_start"], 1)
        self.assertEqual(ref["verse_end"], 5)

    def test_whole_chapter(self):
        ref = parse_reference("Psalm 23")
        self.assertEqual(ref["book"], "Psalms")
        self.assertEqual(ref["verse_start"], None)

    def test_psalms_plural(self):
        self.assertEqual(parse_reference("Psalms 23")["book"], "Psalms")

    def test_abbreviation(self):
        self.assertEqual(parse_reference("Jn 3:16")["book"], "John")

    def test_numbered_book(self):
        ref = parse_reference("1 John 1:9")
        self.assertEqual(ref["book"], "1 John")

    def test_multiword_book(self):
        ref = parse_reference("Song of Solomon 2:1")
        self.assertEqual(ref["book"], "Song of Solomon")

    def test_no_space_before_chapter(self):
        self.assertEqual(parse_reference("John3:16")["book"], "John")

    def test_garbage_rejected(self):
        self.assertIsNone(parse_reference("Not a book 3:16"))

    def test_build_slides_john(self):
        slides = build_slides(parse_reference("John 3:16"))
        self.assertEqual(len(slides), 1)
        self.assertEqual(slides[0]["reference"], "John 3:16")
        # Single-verse slides omit the inline verse number
        self.assertTrue(slides[0]["text"].startswith("For God so loved"))

    def test_build_slides_romans_range(self):
        slides = build_slides(parse_reference("Romans 8:28-30"))
        self.assertGreaterEqual(len(slides), 1)

    def test_build_slides_psalm23(self):
        # per-verse slides now: one slide per verse of the chapter
        slides = build_slides(parse_reference("Psalm 23"))
        self.assertEqual(len(slides), 6)
        self.assertEqual(slides[0]["reference"], "Psalms 23:1")

    def test_build_slides_legacy_grouping(self):
        # per_slide > 1 keeps the old up-to-3-verse grouping
        slides = build_slides(parse_reference("Psalm 23"), per_slide=3)
        self.assertEqual(len(slides), 3)
        self.assertEqual(slides[0]["reference"], "Psalms 23:1-3")

    def test_chapter_slides_john3(self):
        slides = chapter_slides("John", 3)
        self.assertEqual(len(slides), 36)
        self.assertEqual(slides[0]["verse"], 1)
        self.assertEqual(slides[0]["reference"], "John 3:1")
        self.assertEqual(slides[0]["fullRef"], "John 3")
        self.assertEqual(slides[15]["reference"], "John 3:16")
        self.assertTrue(slides[15]["text"].startswith("For God so loved"))

    def test_chapter_slides_psalm119(self):
        self.assertEqual(len(chapter_slides("Psalms", 119)), 176)

    def test_chapter_slides_missing(self):
        self.assertEqual(chapter_slides("John", 99), [])
        self.assertEqual(chapter_slides("Not a Book", 1), [])

    def test_chapter_verse_count(self):
        self.assertEqual(chapter_verse_count("John", 3), 36)
        self.assertEqual(chapter_verse_count("John", 99), 0)

    def test_index_66_books(self):
        index = build_index()
        self.assertEqual(len(index), 66)
        self.assertEqual(sum(1 for b in index if b["testament"] == "OT"), 39)
        self.assertEqual(sum(1 for b in index if b["testament"] == "NT"), 27)
        self.assertEqual(sum(v for b in index for v in b["chapters"]), 31102)
        self.assertEqual(index[0]["name"], "Genesis")
        self.assertEqual(index[39]["name"], "Matthew")

    def test_adjacent_chapter(self):
        self.assertEqual(adjacent_chapter("John", 3, 1), ("John", 4))
        self.assertEqual(adjacent_chapter("John", 4, -1), ("John", 3))
        self.assertEqual(adjacent_chapter("Genesis", 1, -1), None)
        self.assertEqual(adjacent_chapter("Revelation", 22, 1), None)
        self.assertEqual(adjacent_chapter("Malachi", 4, 1), ("Matthew", 1))

    def test_canonicalize(self):
        self.assertEqual(canonicalize("John 3:16")["verse"], 16)
        self.assertEqual(canonicalize({"book": "john", "chapter": 3, "verse": 16})["display"], "John 3:16")
        self.assertEqual(canonicalize({"book": "John", "chapter": 3})["verse"], 0)
        self.assertIsNone(canonicalize({"book": "zzz", "chapter": 1}))
        self.assertIsNone(canonicalize("not a reference"))


class ScriptureTranslationApiTests(django.test.SimpleTestCase):
    def test_translations_api_full_names_and_counts(self):
        resp = self.client.get("/api/scripture/translations")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["translations"]), 9)
        names = [t["full_name"] for t in data["translations"]]
        self.assertIn("King James Version", names)
        self.assertIn("World English Bible", names)
        self.assertIn("Douay-Rheims 1899", names)
        self.assertIn("Yoruba Contemporary Bible", names)
        for t in data["translations"]:
            self.assertEqual(t["book_count"], 66, t)
            self.assertIn("id", t)
            self.assertIn("license", t)
            self.assertGreater(t["verse_count"], 0, t)
        # Full names only — never abbreviated in the payload.
        self.assertNotIn("kjv", names)
        self.assertNotIn("KJV", names)

    def test_index_is_per_translation(self):
        # Douay-Rheims has Vulgate chapter numbering: Esther 16, Daniel 14.
        resp = self.client.get("/api/scripture/index?translation=douay_rheims_1899")
        books = {b["name"]: b for b in resp.json()["books"]}
        self.assertEqual(len(books["Esther"]["chapters"]), 16)
        self.assertEqual(len(books["Daniel"]["chapters"]), 14)
        # Default (King James): Esther 10, Daniel 12.
        resp = self.client.get("/api/scripture/index")
        books = {b["name"]: b for b in resp.json()["books"]}
        self.assertEqual(len(books["Esther"]["chapters"]), 10)
        self.assertEqual(len(books["Daniel"]["chapters"]), 12)

    def test_chapter_preview_honors_translation(self):
        resp = self.client.get(
            "/api/scripture/chapter?book=John&chapter=3&translation=world_english_bible"
        )
        self.assertEqual(resp.status_code, 200)
        slides = resp.json()["slides"]
        self.assertEqual(len(slides), 36)
        self.assertIn("only born Son", slides[15]["text"])


class SongImportTests(django.test.SimpleTestCase):
    def test_plain_text_sections(self):
        text = "[Verse 1]\nLine one\nLine two\n\n[Chorus]\nChorus line"
        sections = parse_import(text, "plain")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["label"], "Verse 1")
        self.assertEqual(sections[1]["label"], "Chorus")

    def test_chordpro_strips_chords(self):
        text = "{verse: 1}\n[C]Amazing [G]grace how\nsweet the sound"
        sections = parse_import(text, "chordpro")
        self.assertEqual(len(sections), 1)
        self.assertNotIn("[", sections[0]["lyrics"])

    def test_clean_hymn_title_strips_comma_and_leading_allcaps(self):
        self.assertEqual(clean_hymn_title("EFI iyin fun Olorun,"), "Efi iyin fun Olorun")
        self.assertEqual(clean_hymn_title("JE k' agb' oju ayo s' oke"), "Je k' agb' oju ayo s' oke")
        self.assertEqual(clean_hymn_title("  GBOGBO eda dapo,  "), "Gbogbo eda dapo")
        # Only the first word is re-cased; apostrophes and later words untouched.
        self.assertEqual(clean_hymn_title("L' OJU ale, 'gbat' orun wo,"), "L' OJU ale, 'gbat' orun wo")
        # Already-clean titles pass through.
        self.assertEqual(clean_hymn_title("A Child Of The King"), "A Child Of The King")

    def test_split_hymn_sections_interleaves_chorus_after_every_verse(self):
        verses = ["v1 text", "v2 text"]
        sections = split_hymn_sections(verses, "chorus lines")
        self.assertEqual([s["label"] for s in sections], ["Verse 1", "Chorus", "Verse 2", "Chorus"])
        self.assertEqual(sections[0]["lyrics"], "v1 text")
        self.assertEqual(sections[1]["lyrics"], "chorus lines")
        self.assertEqual(sections[3]["lyrics"], "chorus lines")

    def test_split_hymn_sections_single_verse_chorus_and_no_chorus(self):
        single = split_hymn_sections(["only verse"], "refrain")
        self.assertEqual([s["label"] for s in single], ["Verse 1", "Chorus"])
        plain = split_hymn_sections(["a", "b", "c"], "")
        self.assertEqual([s["label"] for s in plain], ["Verse 1", "Verse 2", "Verse 3"])
        self.assertEqual(plain[0]["lyrics"], "a")


class HymnalImportTests(django.test.TestCase):
    """DB-backed checks for the bundled-hymnal importer (real files)."""

    def _import(self):
        return import_hymnal_files(settings.BASE_DIR)

    def test_import_creates_975_with_language_and_source(self):
        report = self._import()
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["skipped"], 0)
        self.assertEqual(report["imported"], 975)
        self.assertEqual(Song.objects.count(), 975)
        self.assertEqual(Song.objects.filter(language="Yoruba").count(), 650)
        self.assertEqual(Song.objects.filter(language="English").count(), 325)
        # The hymnal of record is the full name, never the abbreviation.
        self.assertEqual(Song.objects.filter(source="Yoruba Baptist Hymnal").count(), 650)
        self.assertEqual(Song.objects.filter(source="Baptist Hymnal").count(), 325)
        self.assertTrue(all(s.source_abbr in ("YBH", "NNBH")
                            for s in Song.objects.exclude(source_abbr="")))

    def test_import_is_idempotent(self):
        self._import()
        second = self._import()
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["skipped"], 975)
        self.assertEqual(Song.objects.count(), 975)

    def test_yoruba_roundtrip_byte_identical_and_title_cleanup(self):
        self._import()
        song = Song.objects.get(source_abbr="YBH", number=1)
        self.assertEqual(song.title, "Efi iyin fun Olorun")
        self.assertEqual(song.original_title, "EFI iyin fun Olorun,")
        with open(os.path.join(settings.BASE_DIR, "ybh(1).json"), encoding="utf-8") as f:
            data = json.load(f)
        source = next(h for h in data["hymns"] if h["id"] == "ybh_001")
        self.assertEqual(song.sections.get(order=0).lyrics, source["verses"][0])
        # A verse carrying a curly apostrophe (U+2019) must survive unchanged.
        curved = next(
            h for h in data["hymns"] if any("\u2019" in v for v in h.get("verses", []))
        )
        stored = Song.objects.get(source_abbr="YBH", number=curved["metadata"]["number"])
        sections = list(stored.sections.all().order_by("order"))
        self.assertEqual(
            [sec.lyrics for sec in sections if sec.label.startswith("Verse")],
            [v for v in curved["verses"] if str(v).strip()],
        )

    def test_search_shortcut_and_language_filter(self):
        self._import()
        resp = self.client.get("/api/songs", {"q": "nnbh 8"})
        songs = resp.json()["songs"]
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["source"], "Baptist Hymnal")
        self.assertEqual(songs[0]["language"], "English")
        self.assertEqual(songs[0]["number"], 8)
        resp = self.client.get("/api/songs", {"q": "ybh 1"})
        songs = resp.json()["songs"]
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["title"], "Efi iyin fun Olorun")
        self.assertEqual(songs[0]["source"], "Yoruba Baptist Hymnal")
        resp = self.client.get("/api/songs", {"language": "Yoruba"})
        self.assertEqual(len(resp.json()["songs"]), 650)
        resp = self.client.get("/api/songs", {"q": "nnbh 9999"})
        self.assertEqual(resp.json()["songs"], [])


class HymnalPresentTests(django.test.TransactionTestCase):
    """3 English + 3 Yoruba real hymns presented over the WS path.

    Confirms the verse-chorus slide order assumption end-to-end: a hymn with
    a chorus yields V1, Chorus, V2, Chorus, ... — the chorus after every
    verse — and slideCount matches the server's paragraph/6-line chunking.
    """

    async def _present_each(self):
        await sync_to_async(import_hymnal_files)(settings.BASE_DIR)
        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        control = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        await display.connect()
        await control.connect()
        await display.receive_json_from()
        await control.receive_json_from()

        files = {}
        for name in ("ybh(1).json", "nnbh(1).json"):
            with open(os.path.join(settings.BASE_DIR, name), encoding="utf-8") as f:
                files[name] = json.load(f)
        picks = []
        for spec, data in (("NNBH", files["nnbh(1).json"]), ("YBH", files["ybh(1).json"])):
            chorus_hymns = [h for h in data["hymns"] if h.get("chorus", "").strip()]
            chosen = chorus_hymns[:3]
            self.assertTrue(chosen, f"{spec} needs at least one chorus hymn")
            for h in chosen:
                picks.append((spec, h))

        for abbr, hymn in picks:
            song = await sync_to_async(Song.objects.get)(
                source_abbr=abbr, number=hymn["metadata"]["number"]
            )
            await control.send_json_to(
                {"type": "command", "command": {"action": "present_song", "id": song.id}}
            )
            msg = await display.receive_json_from()
            self.assertEqual(msg["type"], "state")
            self.assertEqual(msg["state"]["contentType"], "song")
            content = msg["state"]["content"]

            verses = [v for v in hymn["verses"] if str(v).strip()]
            chorus = hymn.get("chorus", "") or ""
            expected = split_hymn_sections(verses, chorus)
            expected_slides = []
            for sec in expected:
                for chunk in _split_lyrics(sec["lyrics"]):
                    expected_slides.append({"label": sec["label"], "text": chunk})

            self.assertEqual(msg["state"]["slideCount"], len(expected_slides))
            self.assertEqual(len(content["slides"]), len(expected_slides))
            self.assertEqual([s["label"] for s in content["slides"]],
                             [s["label"] for s in expected_slides])
            if chorus.strip():
                labels = [s["label"] for s in content["slides"]]
                self.assertEqual(labels[0], "Verse 1")
                self.assertEqual(labels[1], "Chorus")

        await display.disconnect()
        await control.disconnect()


class SongSearchTests(django.test.TestCase):
    """Search behaviors: bare number, hymnal prefix, body-text search."""

    def _mk(self, abbr, number, title, body=None, chorus=None, original="", language=None, source=None):
        song = Song.objects.create(
            title=title,
            original_title=original or title,
            author="Tester",
            language=language or ("Yoruba" if abbr == "YBH" else "English"),
            source=source or ("Yoruba Baptist Hymnal" if abbr == "YBH" else "Baptist Hymnal"),
            source_abbr=abbr,
            number=number,
        )
        if body:
            SongSection.objects.create(song=song, label="Verse 1", lyrics=body, order=0)
        if chorus:
            SongSection.objects.create(song=song, label="Chorus", lyrics=chorus, order=1)
        return song

    def setUp(self):
        # #23 exists in BOTH hymnals; #8 and #30 in only one each.
        self._mk("YBH", 23, "Yoruba Twenty Three", body="Iwo ni Olorun wa"),
        self._mk("NNBH", 23, "English Twenty Three", body="All the people sing"),
        self._mk("NNBH", 8, "A Mighty Fortress",
                 body="A mighty fortress is our God",
                 original="A MIGHTY FORTRESS 8")
        self._mk("YBH", 30, "Only Yoruba Thirty", body="Mo yin Olorun")
        self._mk("NNBH", 44, "Beauty Divine", chorus="Ring the golden bells, ring them loud")
        self._mk("YBH", 55, "First Line Hymn",
                 body="A line nobody remembers",
                 original="FIRST LINE HYMN 55,")

    def _labels(self, resp):
        # Label matches by full hymnal name + number (abbr is never exposed).
        return {(s["source"], s["number"]) for s in resp.json()["songs"]}

    def test_bare_number_both_hymnals_are_labelled(self):
        resp = self.client.get("/api/songs", {"q": "23"})
        self.assertEqual(self._labels(resp), {("Yoruba Baptist Hymnal", 23), ("Baptist Hymnal", 23)})
        for s in resp.json()["songs"]:
            self.assertIn(s["source"], ("Yoruba Baptist Hymnal", "Baptist Hymnal"))

    def test_bare_number_one_hymnal_only(self):
        resp = self.client.get("/api/songs", {"q": "30"})
        self.assertEqual(self._labels(resp), {("Yoruba Baptist Hymnal", 30)})
        resp = self.client.get("/api/songs", {"q": "8"})
        self.assertEqual(self._labels(resp), {("Baptist Hymnal", 8)})

    def test_bare_number_respects_language_filter(self):
        resp = self.client.get("/api/songs", {"q": "23", "language": "English"})
        self.assertEqual(self._labels(resp), {("Baptist Hymnal", 23)})

    def test_hymnal_prefix_still_works(self):
        resp = self.client.get("/api/songs", {"q": "nnbh 8"})
        self.assertEqual(self._labels(resp), {("Baptist Hymnal", 8)})
        resp = self.client.get("/api/songs", {"q": "ybh 30"})
        self.assertEqual(self._labels(resp), {("Yoruba Baptist Hymnal", 30)})

    def test_text_search_matches_title_case_insensitively(self):
        self.assertEqual(self._labels(self.client.get("/api/songs", {"q": "mighty"})),
                         {("Baptist Hymnal", 8)})
        self.assertEqual(self._labels(self.client.get("/api/songs", {"q": "MIGHTY"})),
                         {("Baptist Hymnal", 8)})

    def test_text_search_matches_phrase_inside_a_verse(self):
        resp = self.client.get("/api/songs", {"q": "Olorun wa"})
        self.assertEqual(self._labels(resp), {("Yoruba Baptist Hymnal", 23)})

    def test_text_search_matches_phrase_inside_a_chorus(self):
        resp = self.client.get("/api/songs", {"q": "ring them loud"})
        self.assertEqual(self._labels(resp), {("Baptist Hymnal", 44)})

    def test_text_search_matches_original_title(self):
        resp = self.client.get("/api/songs", {"q": "hymn 55"})
        self.assertEqual(self._labels(resp), {("Yoruba Baptist Hymnal", 55)})

    def test_zero_results_return_clean_list(self):
        resp = self.client.get("/api/songs", {"q": "zzzznotthere"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["songs"], [])


class ImageDeleteTests(django.test.TestCase):
    """Image deletion: disk+DB clean-up and the in-use background block."""

    def setUp(self):
        self._media = tempfile.mkdtemp()
        self._old_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._media

    def tearDown(self):
        settings.MEDIA_ROOT = self._old_root
        shutil.rmtree(self._media, ignore_errors=True)

    def _upload(self, name="bg.jpg"):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (24, 24), (2, 3, 4)).save(buf, format="JPEG")
        resp = self.client.post("/api/images/upload", {
            "file": SimpleUploadedFile(name, buf.getvalue(), "image/jpeg"),
            "title": name,
        })
        self.assertEqual(resp.status_code, 200)
        return resp.json()["image"]

    def _room_with_background(self, code, url):
        room, _ = Room.objects.get_or_create(code=code)
        room.set_state({"styles": {"background": {"type": "image", "value": url}}})
        return room

    def test_delete_unused_removes_file_db_and_listing(self):
        img = self._upload()
        path = ImageItem.objects.get(id=img["id"]).file.path
        self.assertTrue(os.path.exists(path))

        resp = self.client.delete(f"/api/images/{img['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})

        self.assertFalse(os.path.exists(path))
        self.assertFalse(ImageItem.objects.filter(id=img["id"]).exists())
        listed = {i["id"] for i in self.client.get("/api/images").json()["images"]}
        self.assertNotIn(img["id"], listed)
        gone = self.client.get(f"/api/images/{img['id']}")
        self.assertEqual(gone.status_code, 404)

    def test_delete_in_use_is_blocked_with_room_name(self):
        img = self._upload()
        self._room_with_background("SANCT", img["url"])

        resp = self.client.delete(f"/api/images/{img['id']}")
        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertIn("SANCT", payload["error"])
        self.assertEqual(payload["rooms"], ["SANCT"])

        self.assertTrue(ImageItem.objects.filter(id=img["id"]).exists())
        self.assertTrue(os.path.exists(ImageItem.objects.get(id=img["id"]).file.path))

    def test_block_checks_multiple_rooms(self):
        img = self._upload()
        self._room_with_background("SANCT", img["url"])
        self._room_with_background("YOUTH", img["url"])

        resp = self.client.delete(f"/api/images/{img['id']}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(set(resp.json()["rooms"]), {"SANCT", "YOUTH"})

        # A room on a different background is not reported.
        self._room_with_background("OTHER", "/media/other.jpg")
        resp = self.client.delete(f"/api/images/{img['id']}")
        self.assertEqual(set(resp.json()["rooms"]), {"SANCT", "YOUTH"})

    def test_delete_nonexistent_returns_404(self):
        resp = self.client.delete("/api/images/999999")
        self.assertEqual(resp.status_code, 404)


class WebSocketProtocolTests(django.test.TransactionTestCase):
    """End-to-end: phone (control) -> server -> display over Channels."""

    async def _open_rooms(self):
        # NOTE: Django's runner wraps async test methods in async_to_sync but
        # does not call asyncSetUp, so we wire up communicators in-test.
        song = await sync_to_async(Song.objects.create)(title="Test Song", author="Tester")
        await sync_to_async(SongSection.objects.create)(song=song, label="Verse 1", lyrics="Line one\nLine two", order=0)
        await sync_to_async(SongSection.objects.create)(song=song, label="Chorus", lyrics="Chorus text\nMore chorus", order=1)

        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        control = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        connected, _ = await display.connect()
        self.assertTrue(connected)
        connected, _ = await control.connect()
        self.assertTrue(connected)
        return song.id, display, control

    async def test_protocol(self):
        song_id, display, control = await self._open_rooms()

        # display receives initial state on connect
        init = await display.receive_json_from()
        self.assertEqual(init["type"], "state")
        self.assertEqual(init["state"]["room"], ROOM_CODE)

        # control presents the song -> display gets full state (content once)
        await control.send_json_to({"type": "command", "command": {"action": "present_song", "id": song_id}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["contentType"], "song")
        self.assertEqual(msg["state"]["slideCount"], 2)
        self.assertIn("content", msg["state"])

        # next -> display receives a MINIMAL slide_change, no content
        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 1)
        self.assertEqual(msg["itemId"], song_id)
        self.assertNotIn("content", msg, "minimal message must not carry content")

        # prev -> back to slide 0
        await control.send_json_to({"type": "command", "command": {"action": "prev"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 0)

        # blank -> minimal blank message
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        # Reconnection / get_state: a fresh display that joins next gets the
        # full snapshot, including blank=True and the song content.
        probe = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        connected, _ = await probe.connect()
        self.assertTrue(connected)
        msg = await probe.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertTrue(msg["state"]["blank"])
        self.assertEqual(msg["state"]["content"]["title"], "Test Song")

        # direct get_state round-trip
        await probe.send_json_to({"type": "get_state"})
        msg = await probe.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertTrue(msg["state"]["blank"])

        await probe.disconnect()

        await display.disconnect()
        await control.disconnect()

    async def test_set_style(self):
        _, display, control = await self._open_rooms()
        # consume each socket's own connect-state; the control also receives
        # an echo of every broadcast, so read them in order.
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "set_style",
            "styles": {"font": "serif", "size": "lg", "background": {"type": "color", "value": "#000000"}},
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "style_change")
        self.assertEqual(msg["styles"]["font"], "serif")
        self.assertEqual(msg["styles"]["size"], "lg")
        self.assertEqual(msg["styles"]["background"], {"type": "color", "value": "#000000"})

        # Invalid keys are ignored; valid previous values survive.
        await control.send_json_to({"type": "command", "command": {
            "action": "set_style",
            "styles": {"font": "bogus", "nonsense": 1},
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["styles"]["font"], "serif")

        # Drain the control's two style_change echoes, then ask for state.
        for _ in range(2):
            echo = await control.receive_json_from()
            self.assertEqual(echo["type"], "style_change")

        await control.send_json_to({"type": "get_state"})
        msg = await control.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["styles"]["size"], "lg")

        await display.disconnect()
        await control.disconnect()

    async def test_present_scripture_offline(self):
        # A verse reference loads the whole chapter: John 3 = 36 verse slides,
        # slideIndex points at the verse (verse 16 -> index 15).
        song_id, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.send_json_to({
            "type": "command",
            "command": {"action": "present_scripture", "reference": "John 3:16"},
        })
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["contentType"], "scripture")
        self.assertEqual(msg["state"]["slideCount"], 36)
        self.assertEqual(msg["state"]["slideIndex"], 15)
        self.assertEqual(msg["state"]["content"]["reference"], "John 3")
        self.assertEqual(msg["state"]["content"]["slides"][15]["reference"], "John 3:16")
        await display.disconnect()
        await control.disconnect()

    async def test_present_scripture_verse_offset(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        # {book, chapter, verse} form -> whole chapter, index = verse - 1
        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "book": "John", "chapter": 3, "verse": 16,
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertTrue(msg["state"]["slideIndex"] == 15 and msg["state"]["slideCount"] == 36)

        # next -> minimal slide_change, no content
        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 16)
        self.assertNotIn("content", msg)

        # whole-chapter form: reference without a verse starts at verse 1
        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "book": "Psalms", "chapter": 119,
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["slideCount"], 176)
        self.assertEqual(msg["state"]["slideIndex"], 0)
        await display.disconnect()
        await control.disconnect()

    async def test_present_scripture_invalid_no_broadcast(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        # Unknown verse -> error back to the sender only, no broadcast.
        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "book": "John", "chapter": 3, "verse": 99,
        }})
        err = await control.receive_json_from()
        self.assertEqual(err["type"], "error")

        # The display received nothing for the invalid reference: the next
        # message it gets is the (valid) blank broadcast.
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])
        await display.disconnect()
        await control.disconnect()

    async def test_present_scripture_explicit_translation(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "reference": "John 3:16",
            "translation": "world_english_bible",
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["translation"], "world_english_bible")
        self.assertIn("only born Son", msg["state"]["content"]["slides"][15]["text"])
        await display.disconnect()
        await control.disconnect()

    async def test_present_scripture_defaults_to_king_james(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "reference": "John 3:16",
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["translation"], "king_james_version")
        self.assertIn("only begotten Son", msg["state"]["content"]["slides"][15]["text"])
        await display.disconnect()
        await control.disconnect()

    async def test_switch_translation_then_present_reflects_new(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        # Present in KJV first.
        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "reference": "John 3:16",
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["translation"], "king_james_version")
        kjv_text = msg["state"]["content"]["slides"][15]["text"]

        # Switching translation broadcasts a full snapshot but does NOT
        # re-render the current slide: same content, same position.
        await control.send_json_to({"type": "command", "command": {
            "action": "set_translation", "translation": "world_english_bible",
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["translation"], "world_english_bible")
        self.assertEqual(msg["state"]["contentType"], "scripture")
        self.assertEqual(msg["state"]["slideIndex"], 15)
        self.assertEqual(msg["state"]["content"]["slides"][15]["text"], kjv_text)

        # Presenting again (no explicit translation) uses the new one.
        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "reference": "John 3:16",
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["translation"], "world_english_bible")
        self.assertIn("only born Son", msg["state"]["content"]["slides"][15]["text"])
        await display.disconnect()
        await control.disconnect()

    async def test_set_translation_invalid_no_broadcast(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "set_translation", "translation": "not_a_real_bible",
        }})
        err = await control.receive_json_from()
        self.assertEqual(err["type"], "error")

        # The display received nothing: the next thing it gets is the blank
        # broadcast, and the room translation is still the default.
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])
        await display.disconnect()
        await control.disconnect()

    async def test_scripture_crossing_uses_active_translation(self):
        # Crossing chapter boundaries loads from the active translation too.
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "book": "John", "chapter": 3,
            "translation": "world_english_bible",
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["slideCount"], 36)

        # Last verse of John 3 -> next -> John 4, still WEB text.
        await control.send_json_to({"type": "command", "command": {"action": "goto_slide", "slideIndex": 35}})
        await display.receive_json_from()
        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["translation"], "world_english_bible")
        self.assertEqual(msg["state"]["content"]["reference"], "John 4")
        # WEB wording is distinct from KJV ("Jesus was making and baptizing").
        self.assertIn("making and baptizing", msg["state"]["content"]["slides"][0]["text"])
        await display.disconnect()
        await control.disconnect()

    async def test_scripture_crosses_chapters(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "book": "John", "chapter": 3,
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["content"]["reference"], "John 3")
        self.assertEqual(msg["state"]["slideCount"], 36)

        # jump to the last verse, then next -> cross into John 4 (full state)
        await control.send_json_to({"type": "command", "command": {"action": "goto_slide", "slideIndex": 35}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 35)

        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["content"]["reference"], "John 4")
        self.assertEqual(msg["state"]["slideIndex"], 0)
        self.assertEqual(msg["state"]["slideCount"], 54)

        # prev at John 4:1 crosses back to the end of John 3
        await control.send_json_to({"type": "command", "command": {"action": "prev"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["content"]["reference"], "John 3")
        self.assertEqual(msg["state"]["slideIndex"], 35)
        await display.disconnect()
        await control.disconnect()