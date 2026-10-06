"""
Tests for the local presentation system.

- Scripture reference parsing / slide building (offline KJV)
- Song import parsing (plain text and ChordPro)
- Hymnal import: title/section building, idempotency, byte round-trip
- WebSocket protocol: present -> slide_change (minimal) -> blank (minimal)
"""
import json
import os
import re
import shutil
import tempfile
import io

import django.test
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from churchcast.asgi import application
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageOps

from .consumer import _split_lyrics, split_song_slides
from .scripture import (
    parse_reference,
    build_slides,
    chapter_slides,
    chapter_verse_count,
    build_index,
    adjacent_chapter,
    canonicalize,
)
from .song_import import (
    parse_import,
    parse_import_songs,
    parse_plain_text_songs,
    clean_hymn_title,
    split_hymn_sections,
)
from .management.commands.import_hymnals import import_hymnal_files
from .image_pipeline import ImageProcessError, process_image
from .models import Song, SongSection, Room, QueueItem, Announcement, ImageItem

ROOM_CODE = "WS1"


class VersionedStaticTests(django.test.SimpleTestCase):
    """Automatic cache-busting: {% staticver %} appends the file's mtime."""

    def test_staticver_appends_file_mtime(self):
        from django.template import Context, Template
        path = "presentation/js/control.js"
        out = Template(
            '{% load versioned_static %}{% staticver "' + path + '" %}'
        ).render(Context({}))
        expected = "/static/%s?v=%d" % (
            path,
            int(os.path.getmtime(settings.STATICFILES_DIRS[0] / path)),
        )
        self.assertEqual(out, expected)

    def test_staticver_missing_file_falls_back_plain(self):
        from django.template import Context, Template
        path = "presentation/js/does_not_exist.js"
        out = Template(
            '{% load versioned_static %}{% staticver "' + path + '" %}'
        ).render(Context({}))
        self.assertEqual(out, "/static/" + path)


class FontAssetTests(django.test.SimpleTestCase):
    """Self-hosted fonts: declared only locally, all referenced files on disk."""

    def _static(self, *parts):
        return settings.STATICFILES_DIRS[0].joinpath(*parts)

    def test_fonts_css_all_local_with_no_external_urls(self):
        css = self._static("presentation", "css", "fonts.css")
        text = css.read_text()
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("@import", text)
        self.assertIn("@font-face", text)

    def test_every_font_file_referenced_exists_and_vice_versa(self):
        import re
        css_path = self._static("presentation", "css", "fonts.css")
        text = css_path.read_text()
        refs = set(re.findall(r"url\(\s*['\"]?\.\./fonts/([\w.-]+\.woff2)['\"]?\)", text))
        self.assertTrue(len(refs) >= 24, "expected the bundled font face set")
        fonts_dir = self._static("presentation", "fonts")
        for name in refs:
            self.assertTrue((fonts_dir / name).is_file(), f"missing {name}")
        disk = {p.name for p in fonts_dir.iterdir() if p.suffix == ".woff2"}
        self.assertEqual(refs, disk, "fonts.css and fonts/ must be in sync")

    def test_no_external_font_content_anywhere(self):
        # "fonts.googleapis.com" may appear in fonts.css prose (download note),
        # but real loading must stay local: no CDN host and no http(s) url().
        import re
        forbidden = ("fonts.gstatic.com", "cdn.jsdelivr.net", "@import")
        import pathlib
        targets = [
            self._static("presentation", "css", "fonts.css"),
            self._static("presentation", "css", "app.css"),
            self._static("presentation", "css", "display.css"),
            self._static("presentation", "js", "control.js"),
            self._static("presentation", "js", "display.js"),
        ]
        templates = pathlib.Path(__file__).parent / "templates" / "presentation"
        targets += sorted(templates.glob("*.html"))
        for path in targets:
            text = path.read_text()
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{path.name} must not reference {needle}")
            if path.suffix == ".css":
                self.assertIsNone(
                    re.search(r"url\(\s*['\"]?https?://", text),
                    f"{path.name} must not fetch a font over http(s)",
                )

    def test_coverage_and_licence_files_present(self):
        fonts_dir = self._static("presentation", "fonts")
        self.assertTrue((fonts_dir / "COVERAGE.txt").is_file())
        licences = {p.name for p in (fonts_dir / "LICENSES").iterdir()}
        self.assertIn("noto-sans-OFL.txt", licences)
        self.assertIn("noto-serif-OFL.txt", licences)
        self.assertIn("montserrat-OFL.txt", licences)
        self.assertIn("merriweather-OFL.txt", licences)


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


class SongParsingTests(django.test.SimpleTestCase):
    """Number/verbatim-marker plain-text importer: titles, chorus convention,
    multi-song pastes, warnings and byte-for-byte lyric fidelity."""

    SAMPLE_A = (
        "Holy, Holy, Holy\n"
        "1. Holy, holy, holy! Lord God Almighty!\n"
        "Early in the morning our song Shall rise to Thee;\n"
        "Holy, holy, holy\u2019 merciful and mighty!\n"
        "God in three Persons, blessed Trinity!\n"
        "\n"
        "2. Holy, holy, holy! All the saints adore Thee,\n"
        "Casting down their golden Crowns around the glassy sea;\n"
        "Cherubim and Seraphim, falling down before Thee\n"
        "Who wert, and art, and evermore shalt be.\n"
        "\n"
        "3. Holy, holy, holy! tho the darkness hide Thee;\n"
        "Tho\u2019 the eye of sinful man Thy glory may not see;\n"
        "Only Thou art holy, there is none beside Thee;\n"
        "Perfect in power, in love and purity.\n"
        "\n"
        "4. Holy, holy, holy! Lord God Almighty!\n"
        "All Thy works shall praise Thy name, in earth, and sky and sea;\n"
        "Holy, holy, holy; merciful and mighty!\n"
        "God in three persons, blessed Trinity! Amen."
    )

    SAMPLE_B = (
        "Niwaju \u2018te Jehofa nla\n"
        "1. Niwaju \u2018te Jehofa nla,\n"
        "Oril\u2019-ede e f\u2019 ayo sin;\n"
        "Mo p\u2019 On nikan ni Olorun,\n"
        "O le da, Osi le parun.\n"
        "\n"
        "2. Ipa Re laisi \u2018ranwo wa,\n"
        "L\u2019 O f' amo da wa l\u2019 enia;\n"
        "Nigbat\u2019 a sako b\u2019 agutan,\n"
        "O mu wa bo si agbo Re.\n"
        "\n"
        "3. Enia at\u2019 ike Re ni wa,\n"
        "Emi at\u2019 ara iku wa,\n"
        "Ola t\u2019 o to wo l\u2019 a ba fun\n"
        "Oruko Re Eleda Nla.\n"
        "\n"
        "4. Ao fi orin kun ile Re,\n"
        "L\u2019 ohun giga l\u2019 a o korin,\n"
        "Ile y\u2019o f\u2019 egbarun ahon\n"
        "Fi iyin kun agbala Re.\n"
        "\n"
        "5. Ase Re gboro bi aiye,\n"
        "Ife Re bi aiyeraiye,\n"
        "Oto Re y'o duro sinsin,\n"
        "Nigba odun ki y'o yi mo."
    )

    EXPECTED_A = [
        {"label": "Verse 1", "lyrics": (
            "Holy, holy, holy! Lord God Almighty!\n"
            "Early in the morning our song Shall rise to Thee;\n"
            "Holy, holy, holy\u2019 merciful and mighty!\n"
            "God in three Persons, blessed Trinity!")},
        {"label": "Verse 2", "lyrics": (
            "Holy, holy, holy! All the saints adore Thee,\n"
            "Casting down their golden Crowns around the glassy sea;\n"
            "Cherubim and Seraphim, falling down before Thee\n"
            "Who wert, and art, and evermore shalt be.")},
        {"label": "Verse 3", "lyrics": (
            "Holy, holy, holy! tho the darkness hide Thee;\n"
            "Tho\u2019 the eye of sinful man Thy glory may not see;\n"
            "Only Thou art holy, there is none beside Thee;\n"
            "Perfect in power, in love and purity.")},
        {"label": "Verse 4", "lyrics": (
            "Holy, holy, holy! Lord God Almighty!\n"
            "All Thy works shall praise Thy name, in earth, and sky and sea;\n"
            "Holy, holy, holy; merciful and mighty!\n"
            "God in three persons, blessed Trinity! Amen.")},
    ]

    EXPECTED_B = [
        {"label": "Verse 1", "lyrics": (
            "Niwaju \u2018te Jehofa nla,\n"
            "Oril\u2019-ede e f\u2019 ayo sin;\n"
            "Mo p\u2019 On nikan ni Olorun,\n"
            "O le da, Osi le parun.")},
        {"label": "Verse 2", "lyrics": (
            "Ipa Re laisi \u2018ranwo wa,\n"
            "L\u2019 O f' amo da wa l\u2019 enia;\n"
            "Nigbat\u2019 a sako b\u2019 agutan,\n"
            "O mu wa bo si agbo Re.")},
        {"label": "Verse 3", "lyrics": (
            "Enia at\u2019 ike Re ni wa,\n"
            "Emi at\u2019 ara iku wa,\n"
            "Ola t\u2019 o to wo l\u2019 a ba fun\n"
            "Oruko Re Eleda Nla.")},
        {"label": "Verse 4", "lyrics": (
            "Ao fi orin kun ile Re,\n"
            "L\u2019 ohun giga l\u2019 a o korin,\n"
            "Ile y\u2019o f\u2019 egbarun ahon\n"
            "Fi iyin kun agbala Re.")},
        {"label": "Verse 5", "lyrics": (
            "Ase Re gboro bi aiye,\n"
            "Ife Re bi aiyeraiye,\n"
            "Oto Re y'o duro sinsin,\n"
            "Nigba odun ki y'o yi mo.")},
    ]

    def test_sample_a_title_and_sections_exact(self):
        songs = parse_import_songs(self.SAMPLE_A, "plain")
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["title"], "Holy, Holy, Holy")
        self.assertTrue(songs[0]["title_found"])
        self.assertEqual(songs[0]["sections"], self.EXPECTED_A)
        self.assertEqual(songs[0]["warnings"], [])

    def test_sample_b_title_and_sections_exact(self):
        songs = parse_import_songs(self.SAMPLE_B, "plain")
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["title"], "Niwaju \u2018te Jehofa nla")
        self.assertTrue(songs[0]["title_found"])
        self.assertEqual(songs[0]["sections"], self.EXPECTED_B)
        self.assertEqual(songs[0]["warnings"], [])

    def test_crlf_paste_parses_identically(self):
        for sample, expected in ((self.SAMPLE_A, self.EXPECTED_A),
                                 (self.SAMPLE_B, self.EXPECTED_B)):
            songs = parse_import_songs(sample.replace("\n", "\r\n"), "plain")
            self.assertEqual(songs[0]["sections"], expected)
            self.assertTrue(songs[0]["title_found"])

    def test_verse_marker_variants(self):
        text = ("1.Holy, holy\n"
                "2) O mp a\n"
                "Verse 3: third\n"
                "V4 goto\n"
                "5 fifth")
        songs = parse_import_songs(text, "plain")
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Verse 2", "Verse 3", "Verse 4", "Verse 5"])
        self.assertEqual([s["lyrics"] for s in songs[0]["sections"]],
                         ["Holy, holy", "O mp a", "third", "goto", "fifth"])
        self.assertIn("No title found", songs[0]["warnings"])

    def test_title_may_have_blank_line_before_verse_1(self):
        songs = parse_import_songs("My Great Song\n\n1. Line one\n\n2. Line two", "plain")
        self.assertEqual(songs[0]["title"], "My Great Song")
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Verse 2"])

    def test_chorus_explicitly_between_verses_keeps_paste_order(self):
        text = "1. A text\n\nChorus\nC1\n\n2. B text"
        songs = parse_import_songs(text, "plain")
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Chorus", "Verse 2"])
        self.assertEqual(songs[0]["sections"][1]["lyrics"], "C1")

    def test_chorus_convention_interleaves_after_every_verse(self):
        text = "1. A text\n\n2. B text\n\nChorus\nC1"
        songs = parse_import_songs(text, "plain")
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Chorus", "Verse 2", "Chorus"])
        self.assertEqual(songs[0]["sections"][1]["lyrics"], "C1")
        self.assertEqual(songs[0]["sections"][3]["lyrics"], "C1")

    def test_named_markers_refrain_bridge_tag(self):
        text = ("1. First\n\n"
                "Refrain: r1\n\n"
                "2. Second\n\n"
                "Bridge\nb1\n\n"
                "Tag\nbye")
        songs = parse_import_songs(text, "plain")
        labels = [s["label"] for s in songs[0]["sections"]]
        # "Refrain" sits explicitly between two verses, so paste order wins —
        # no chorus interleave. Bridge/Tag stay in place.
        self.assertEqual(labels, ["Verse 1", "Chorus", "Verse 2", "Bridge", "Tag"])
        self.assertEqual(songs[0]["sections"][3]["lyrics"], "b1")
        self.assertEqual(songs[0]["sections"][4]["lyrics"], "bye")

    def test_no_title_when_paste_starts_with_a_verse_marker(self):
        songs = parse_import_songs("1. A line\n2. B line", "plain")
        self.assertEqual(songs[0]["title"], "")
        self.assertFalse(songs[0]["title_found"])
        self.assertIn("No title found", songs[0]["warnings"])

    def test_skipped_numbering_warning(self):
        songs = parse_import_songs("1. A\n2. B\n4. D", "plain")
        self.assertIn("Numbered verses skip: 3", songs[0]["warnings"])
        # Labels follow slide order, not the (gappy) source numbers.
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Verse 2", "Verse 3"])

    def test_empty_section_warning(self):
        songs = parse_import_songs("1.\n2. B", "plain")
        self.assertTrue(any("Empty section" in w for w in songs[0]["warnings"]))

    def test_two_songs_from_a_numbering_restart_paste(self):
        combined = self.SAMPLE_A + "\n\n" + self.SAMPLE_B
        songs = parse_import_songs(combined, "plain")
        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0]["title"], "Holy, Holy, Holy")
        self.assertEqual(songs[0]["sections"], self.EXPECTED_A)
        self.assertEqual(songs[1]["title"], "Niwaju \u2018te Jehofa nla")
        self.assertEqual(songs[1]["sections"], self.EXPECTED_B)

    def test_single_song_verse_restart_with_blank_boundary_stays_multi(self):
        text = ("1. A line\n2. B line\n\n"
                "Another Song\n1. X\n2. Y")
        songs = parse_import_songs(text, "plain")
        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[1]["title"], "Another Song")
        self.assertEqual([s["label"] for s in songs[1]["sections"]],
                         ["Verse 1", "Verse 2"])

    def test_yoruba_round_trip_is_byte_identical(self):
        # U+2019 / U+2018 elision marks, ASCII apostrophes and diacritics must
        # survive verbatim — sections are compared exactly above; assert the
        # tricky characters are present, not escaped or replaced.
        songs = parse_import_songs(self.SAMPLE_B, "plain")
        joined = "\n".join(s["lyrics"] for s in songs[0]["sections"])
        for token in ("\u2018te Jehofa nla", "Oril\u2019-ede", "f' amo",
                      "Nigbat\u2019", "Oto Re y'o", "\u2018ranwo"):
            self.assertIn(token, joined)

    def test_blank_line_paragraph_fallback_one_section_per_paragraph(self):
        text = "First paragraph line one\nfirst two\n\nSecond paragraph\nsecond two\n\nThird"
        songs = parse_import_songs(text, "plain")
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Verse 2", "Verse 3"])
        self.assertEqual(songs[0]["sections"][2]["lyrics"], "Third")

    def test_bracket_plain_format_still_works(self):
        text = "[Verse 1]\nLine one\nLine two\n\n[Chorus]\nChorus line"
        songs = parse_import_songs(text, "plain")
        self.assertEqual([s["label"] for s in songs[0]["sections"]],
                         ["Verse 1", "Chorus"])

    def test_empty_input_yields_one_empty_song(self):
        songs = parse_import_songs("", "plain")
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["sections"], [])
        self.assertEqual(parse_import("", "plain"), [])


class SongSlideSplittingTests(django.test.SimpleTestCase):
    """Slide chunking: ≤6 lines on one slide, longer content balanced."""

    def test_shared_fixture_matches_server_splitter(self):
        with open(os.path.join(settings.BASE_DIR, "presentation", "data",
                               "song_slides_fixtures.json"), encoding="utf-8") as fh:
            fixture = json.load(fh)
        for case in fixture["cases"]:
            self.assertEqual(
                split_song_slides(case["lyrics"]), case["expected"],
                f"server splitter must match fixture case: {case['name']}")

    def test_sample_a_one_slide_per_verse(self):
        song = parse_import_songs(SongParsingTests.SAMPLE_A, "plain")[0]
        slides = [chunk for sec in song["sections"] for chunk in split_song_slides(sec["lyrics"])]
        self.assertEqual(len(slides), 4)
        self.assertEqual([s.count("\n") + 1 for s in slides], [4, 4, 4, 4])

    def test_sample_b_five_slides(self):
        song = parse_import_songs(SongParsingTests.SAMPLE_B, "plain")[0]
        slides = [chunk for sec in song["sections"] for chunk in split_song_slides(sec["lyrics"])]
        self.assertEqual(len(slides), 5)

    def test_ten_line_section_splits_5_plus_5(self):
        lines = [f"L{i}" for i in range(1, 11)]
        self.assertEqual(split_song_slides("\n".join(lines)),
                         ["\n".join(lines[:5]), "\n".join(lines[5:])])

    def test_split_lyrics_compat_alias_agrees(self):
        text = "\n".join(f"L{i}" for i in range(1, 11))
        self.assertEqual(_split_lyrics(text), split_song_slides(text))
        self.assertEqual(_split_lyrics("a\nb\nc\nd"), ["a\nb\nc\nd"])


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


class ImagePipelineTests(django.test.SimpleTestCase):
    """Pillow upload pipeline: validation, orientation, downscale, variants."""

    def _jpg(self, w, h, color=(120, 40, 90)):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color).save(buf, format="JPEG")
        return buf.getvalue()

    def _png(self, w, h, color=(120, 40, 90)):
        buf = io.BytesIO()
        Image.new("RGBA", (w, h), color).save(buf, format="PNG")
        return buf.getvalue()

    def _main_size(self, result):
        return Image.open(io.BytesIO(result["main"])).size

    def test_exif_rotated_upright(self):
        # Raw 300x200 photo tagged orientation 6 must come out upright
        # (200x300), the way ImageOps.exif_transpose handles it.
        buf = io.BytesIO()
        im = Image.new("RGB", (300, 200), (10, 20, 30))
        exif = im.getexif()
        exif[0x0112] = 6
        im.save(buf, format="JPEG", exif=exif)
        result = process_image(buf.getvalue(), "portrait.jpg")
        rotated = Image.open(io.BytesIO(result["main"]))
        self.assertEqual(rotated.size, (200, 300))

    def test_big_landscape_downscaled_to_1920(self):
        result = process_image(self._jpg(4000, 3000), "big.jpg")
        self.assertEqual(self._main_size(result), (1920, 1440))

    def test_small_image_never_upscaled(self):
        result = process_image(self._jpg(800, 600), "small.jpg")
        self.assertEqual(self._main_size(result), (800, 600))

    def test_portrait_dimensions_preserved(self):
        result = process_image(self._jpg(3000, 4000), "portrait.jpg")
        self.assertEqual(self._main_size(result), (1440, 1920))

    def test_png_alpha_stays_png(self):
        result = process_image(self._png(400, 300), "alpha.png")
        self.assertEqual(result["ext"], "png")
        self.assertEqual(Image.open(io.BytesIO(result["main"])).mode, "RGBA")

    def test_variants_exist_and_are_small(self):
        result = process_image(self._jpg(2000, 1000), "photo.jpg")
        for kind in ("thumb", "soft", "strong"):
            self.assertIn(kind, result["variants"])
        # thumb/strong are the small tiles; soft is the 1280 backdrop.
        self.assertEqual(Image.open(io.BytesIO(result["variants"]["thumb"])).size, (320, 160))
        self.assertEqual(Image.open(io.BytesIO(result["variants"]["strong"])).size, (320, 160))
        soft = Image.open(io.BytesIO(result["variants"]["soft"])).size
        self.assertEqual(soft, (1280, 640))

    def test_text_renamed_jpg_rejected(self):
        with self.assertRaises(ImageProcessError) as cm:
            process_image(b"definitely not an image", "report.txt.jpg")
        self.assertIn("not a readable", cm.exception.message)

    def test_svg_rejected(self):
        with self.assertRaises(ImageProcessError) as cm:
            process_image(b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>", "logo.svg")
        self.assertIn("not a readable", cm.exception.message)

    def test_animated_png_rejected(self):
        # A two-frame (APNG) upload is refused outright, not first-frame-fixed.
        frames = [Image.new("RGBA", (100, 100), (200, 0, 0)), Image.new("RGBA", (100, 100), (0, 0, 200))]
        buf = io.BytesIO()
        frames[0].save(buf, format="PNG", save_all=True, append_images=[frames[1]], duration=200)
        with self.assertRaises(ImageProcessError) as cm:
            process_image(buf.getvalue(), "anim.png")
        self.assertIn("Animated images are not supported", cm.exception.message)

    def test_heic_filename_rejected_clearly(self):
        with self.assertRaises(ImageProcessError) as cm:
            process_image(b"\x00\x00\x00 HEIC junk", "IMG_0001.HEIC")
        self.assertIn("HEIC/HEIF", cm.exception.message)

    def test_heic_bytes_rejected_without_dependency(self):
        # Real HEIC/HEIF payloads (ISO BMFF 'ftyp' marker) are caught by the
        # brand check even with no filename: no Pillow HEIF plugin needed.
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00hei1mif1\x00\x00"
        with self.assertRaises(ImageProcessError) as cm:
            process_image(heic, "")
        self.assertEqual(cm.exception.message.split()[0], "HEIC/HEIF")

    def test_avif_ftyp_rejected_as_unsupported(self):
        # AVIF shares the ISO BMFF container but is not HEIC; still rejected.
        avif = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifav01\x00\x00"
        with self.assertRaises(ImageProcessError) as cm:
            process_image(avif, "clip.avif")
        self.assertIn("not supported", cm.exception.message)


class ImageUploadTests(django.test.TestCase):
    """The upload API end-to-end (with a throwaway media root)."""

    def setUp(self):
        self._media = tempfile.mkdtemp()
        self._old_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._media

    def tearDown(self):
        settings.MEDIA_ROOT = self._old_root
        shutil.rmtree(self._media, ignore_errors=True)

    def _upload(self, w=240, h=240, name="photo.jpg", fmt="JPEG"):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (60, 80, 120)).save(buf, format=fmt)
        return self.client.post("/api/images/upload", {
            "file": SimpleUploadedFile(name, buf.getvalue(), "image/jpeg"),
            "title": name,
        })

    def _disk_paths(self, img_id):
        item = ImageItem.objects.get(id=img_id)
        return item.all_file_names(), [item.file.path]

    def test_upload_creates_all_variant_files(self):
        store = ImageItem._meta.get_field("file").storage
        resp = self._upload(1200, 900)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["image"]
        item = ImageItem.objects.get(id=data["id"])
        self.assertTrue(item.file_thumb and item.file_soft and item.file_strong)
        self.assertTrue(item.sha256)
        for name in item.all_file_names():
            self.assertTrue(store.exists(name), "missing file: " + name)
        self.assertTrue(data["thumb"].startswith("/media/"))
        for k in ("soft", "strong"):
            self.assertTrue(data[k].startswith("/media/"), k)

    def test_duplicate_upload_skipped_and_reported(self):
        first = self._upload()
        self.assertEqual(first.status_code, 200)
        second = self._upload()
        payload = second.json()
        self.assertTrue(payload["duplicate"])
        self.assertEqual(payload["image"]["sha256"], first.json()["image"]["sha256"])
        self.assertEqual(ImageItem.objects.count(), 1)

    def test_duplicate_of_refit_image_points_at_existing_without_reset(self):
        # Upload -> refit to "cover" -> upload the same bytes again: the
        # duplicate report must reference the SAME image and must NOT reset
        # its fit back to the default.
        first = self._upload()
        img_id = first.json()["image"]["id"]
        self.client.post("/api/images/%d" % img_id, {"fit": "cover"}, content_type="application/json")

        second = self._upload()
        payload = second.json()
        self.assertTrue(payload["duplicate"])
        self.assertEqual(payload["image"]["id"], img_id)
        self.assertEqual(payload["image"]["fit"], "cover")
        self.assertEqual(ImageItem.objects.count(), 1)
        self.assertEqual(ImageItem.objects.get(id=img_id).fit, "cover")

    def test_duplicate_of_contain_fit_image_points_at_existing_without_reset(self):
        # Upload -> refit to "contain" -> upload the same bytes again: the
        # duplicate report must reference the SAME image and must NOT reset
        # its fit back to the default.
        first = self._upload()
        img_id = first.json()["image"]["id"]
        self.client.post("/api/images/%d" % img_id, {"fit": "contain"}, content_type="application/json")

        second = self._upload()
        payload = second.json()
        self.assertTrue(payload["duplicate"])
        self.assertEqual(payload["image"]["id"], img_id)
        self.assertEqual(payload["image"]["fit"], "contain")
        self.assertEqual(ImageItem.objects.count(), 1)
        self.assertEqual(ImageItem.objects.get(id=img_id).fit, "contain")

    def test_batch_with_one_bad_file_still_stores_good_ones(self):
        good = self._upload()
        self.assertEqual(good.status_code, 200)
        bad = self.client.post("/api/images/upload", {
            "file": SimpleUploadedFile("notes.txt.jpg", b"this is text", "image/jpeg"),
            "title": "notes",
        })
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not a readable", bad.json()["error"])
        self.assertEqual(ImageItem.objects.count(), 1)

    def test_downscales_large_upload_on_disk(self):
        resp = self._upload(4000, 3000, "huge.jpg")
        item_path = ImageItem.objects.get(id=resp.json()["image"]["id"]).file.path
        stored = Image.open(item_path)
        self.assertEqual(stored.size, (1920, 1440))

    def test_over_15mb_rejected_before_processing(self):
        big = b"x" * (16 * 1024 * 1024)
        resp = self.client.post("/api/images/upload", {
            "file": SimpleUploadedFile("big.jpg", big, "image/jpeg"),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("15 MB", resp.json()["error"])

    def test_legacy_image_lazy_variants(self):
        # Simulate an image uploaded before variants existed: main file only.
        from django.core.files.base import ContentFile
        store = ImageItem._meta.get_field("file").storage
        buf = io.BytesIO()
        Image.new("RGB", (900, 600), (5, 5, 5)).save(buf, format="JPEG")
        img = ImageItem(title="Legacy", fit=None)
        img.file.save("legacy.jpg", ContentFile(buf.getvalue()), save=True)
        self.assertEqual(img.file.name, "images/legacy.jpg")
        self.assertEqual(img.file_thumb, "")

        got = self.client.get("/api/images/%d" % img.id).json()["image"]
        self.assertTrue(got["thumb"] and got["soft"] and got["strong"])
        item = ImageItem.objects.get(id=img.id)
        for name in item.all_file_names():
            self.assertTrue(store.exists(name), "missing variant: " + name)
        # Files have uuid-style names, so a second run is a clean overwrite.
        self.assertEqual(item.file.name, "images/legacy.jpg")

    def test_fit_save_and_reject(self):
        resp = self._upload()
        img_id = resp.json()["image"]["id"]
        post = self.client.post("/api/images/%d" % img_id, {"fit": "cover"}, content_type="application/json")
        self.assertEqual(post.json()["image"]["fit"], "cover")
        # Invalid fit values are ignored; the stored value survives.
        bad = self.client.post("/api/images/%d" % img_id, {"fit": "weird"}, content_type="application/json")
        self.assertEqual(bad.json()["image"]["fit"], "cover")


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


class ImageDeleteVariantTests(ImageDeleteTests):
    """Variant files join the original on deletion; blur-in-use still blocks."""

    def _png_bytes(self, w=200, h=200):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (1, 2, 3)).save(buf, format="PNG")
        return buf.getvalue()

    def test_delete_removes_every_variant_file(self):
        store = ImageItem._meta.get_field("file").storage
        img = self._upload()
        item = ImageItem.objects.get(id=img["id"])
        files = list(item.all_file_names())
        self.assertEqual(len(files), 4)
        for name in files:
            self.assertTrue(store.exists(name))

        resp = self.client.delete("/api/images/%d" % img["id"])
        self.assertEqual(resp.status_code, 200)
        for name in files:
            self.assertFalse(store.exists(name), "file survived delete: " + name)
        self.assertFalse(ImageItem.objects.filter(id=img["id"]).exists())

    def test_delete_blocked_when_background_uses_any_blur_level(self):
        # The in-use check compares the main URL only; a strong-blur
        # background still references the same base image and must block.
        upload = self.client.post("/api/images/upload", {
            "file": SimpleUploadedFile("bg.png", self._png_bytes(), "image/png"),
        })
        item = ImageItem.objects.get(id=upload.json()["image"]["id"])
        Room.objects.create(code="SANCT", state=json.dumps({
            "styles": {"background": {"type": "image", "value": item.file.url, "blur": "strong"}},
        }))

        blocked = self.client.delete("/api/images/%d" % item.id)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("SANCT", blocked.json()["error"])

    def test_delete_blocked_while_presented_in_room(self):
        img = self._upload()
        Room.objects.create(code="SANCT", state=json.dumps({
            "contentType": "image", "itemId": img["id"],
        }))

        blocked = self.client.delete("/api/images/%d" % img["id"])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["rooms"], ["SANCT"])
        self.assertIn("presented", blocked.json()["error"])
        # The image is NOT silently deleted behind the refusal.
        self.assertTrue(ImageItem.objects.filter(id=img["id"]).exists())

    def test_delete_blocked_while_queued_elsewhere_and_allowed_after_removal(self):
        img = self._upload()
        # A room other than where the operator is standing has the image
        # queued by ref_id. The refusal must name the room and tell the
        # operator to remove it from the queue first — never delete the item.
        other = Room.objects.create(code="YOUTH")
        qi = QueueItem.objects.create(room=other, order=0, item_type="image", ref_id=img["id"], ref_data="{}")

        blocked = self.client.delete("/api/images/%d" % img["id"])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["rooms"], ["YOUTH"])
        self.assertIn("Remove it from the service/queue", blocked.json()["error"])
        self.assertTrue(QueueItem.objects.filter(id=qi.id).exists())
        self.assertTrue(ImageItem.objects.filter(id=img["id"]).exists())

        # Once the queue item is gone the delete is allowed.
        qi.delete()
        ok = self.client.delete("/api/images/%d" % img["id"])
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"ok": True})
        self.assertFalse(ImageItem.objects.filter(id=img["id"]).exists())

    def test_delete_reports_presented_and_queued_together(self):
        img = self._upload()
        Room.objects.create(code="SANCT", state=json.dumps({
            "contentType": "image", "itemId": img["id"],
        }))
        Room.objects.create(code="YOUTH", state=json.dumps({}))
        QueueItem.objects.create(room=Room.objects.get(code="YOUTH"), order=0, item_type="image", ref_id=img["id"], ref_data="{}")

        blocked = self.client.delete("/api/images/%d" % img["id"])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(set(blocked.json()["rooms"]), {"SANCT", "YOUTH"})
        self.assertIn("presented", blocked.json()["error"])
        self.assertIn("queued", blocked.json()["error"])


class WebSocketProtocolTests(django.test.TransactionTestCase):
    """End-to-end: phone (control) -> server -> display over Channels."""

    def setUp(self):
        self._tp_media = tempfile.mkdtemp()
        self._tp_old_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._tp_media

    def tearDown(self):
        settings.MEDIA_ROOT = self._tp_old_root
        shutil.rmtree(self._tp_media, ignore_errors=True)

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

    async def _open_queued_songs(self, count):
        """Seed a Room with `count` two-slide queue songs and open both
        sockets. Returns (song_ids, display, control)."""
        songs = []
        for i in range(count):
            s = await sync_to_async(Song.objects.create)(title=f"Queue Song {i + 1}", author="Tester")
            await sync_to_async(SongSection.objects.create)(song=s, label="Verse 1", lyrics="Line one\nLine two", order=0)
            await sync_to_async(SongSection.objects.create)(song=s, label="Chorus", lyrics="Chorus text\nMore chorus", order=1)
            songs.append(s)
        room = await sync_to_async(Room.objects.create)(code=ROOM_CODE)
        for i, s in enumerate(songs):
            await sync_to_async(QueueItem.objects.create)(room=room, order=i, item_type="song", ref_id=s.id, ref_data="{}")
        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        control = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        connected, _ = await display.connect()
        self.assertTrue(connected)
        connected, _ = await control.connect()
        self.assertTrue(connected)
        return [s.id for s in songs], display, control

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

        # Last verse of John 3 -> next_chapter -> John 4, still WEB text.
        await control.send_json_to({"type": "command", "command": {"action": "goto_slide", "slideIndex": 35}})
        await display.receive_json_from()
        await control.send_json_to({"type": "command", "command": {"action": "next_chapter"}})
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

        # jump to the last verse; plain next is now a strict no-op (no auto
        # chapter advance, no broadcast) — the black marker is the next message.
        await control.send_json_to({"type": "command", "command": {"action": "goto_slide", "slideIndex": 35}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 35)
        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        # explicit chapter nav crosses into John 4 (full state)
        await control.send_json_to({"type": "command", "command": {"action": "next_chapter"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["content"]["reference"], "John 4")
        self.assertEqual(msg["state"]["slideIndex"], 0)
        self.assertEqual(msg["state"]["slideCount"], 54)

        # prev at John 4:1 does not auto-cross back either...
        await control.send_json_to({"type": "command", "command": {"action": "prev"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": False}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertFalse(msg["blank"])

        # ...but prev_chapter returns to the end of John 3
        await control.send_json_to({"type": "command", "command": {"action": "prev_chapter"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["content"]["reference"], "John 3")
        self.assertEqual(msg["state"]["slideIndex"], 35)
        await display.disconnect()
        await control.disconnect()

    # --- Queue boundary / item navigation ---

    async def test_queue_slide_nav_stops_at_item_boundary(self):
        _, display, control = await self._open_queued_songs(2)
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 0}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["queueIndex"], 0)
        self.assertEqual(msg["state"]["slideCount"], 2)

        # Last slide of item 0: next must NOT cross into item 1 — no broadcast.
        await control.send_json_to({"type": "command", "command": {"action": "goto_slide", "slideIndex": 1}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 1)
        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        # Position is preserved server-side.
        probe = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        connected, _ = await probe.connect()
        self.assertTrue(connected)
        msg = await probe.receive_json_from()
        self.assertEqual(msg["state"]["queueIndex"], 0)
        self.assertEqual(msg["state"]["slideIndex"], 1)
        self.assertEqual(msg["state"]["contentType"], "song")
        await probe.disconnect()

        # prev moves within the item: back to slide 0.
        await control.send_json_to({"type": "command", "command": {"action": "prev"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        self.assertEqual(msg["slideIndex"], 0)

        # prev at the first slide is a no-op too.
        await control.send_json_to({"type": "command", "command": {"action": "prev"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": False}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertFalse(msg["blank"])
        await display.disconnect()
        await control.disconnect()

    async def test_item_nav_moves_between_adjacent_items(self):
        song_ids, display, control = await self._open_queued_songs(2)
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 0}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["queueIndex"], 0)
        self.assertEqual(msg["state"]["content"]["title"], "Queue Song 1")

        await control.send_json_to({"type": "command", "command": {"action": "next_item"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["queueIndex"], 1)
        self.assertEqual(msg["state"]["itemId"], song_ids[1])
        self.assertEqual(msg["state"]["slideIndex"], 0)
        self.assertEqual(msg["state"]["slideCount"], 2)
        self.assertIn("content", msg["state"])

        await control.send_json_to({"type": "command", "command": {"action": "prev_item"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["queueIndex"], 0)
        self.assertEqual(msg["state"]["itemId"], song_ids[0])
        self.assertEqual(msg["state"]["slideIndex"], 0)
        await display.disconnect()
        await control.disconnect()

    async def test_item_nav_boundaries_are_noops(self):
        _, display, control = await self._open_queued_songs(2)
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 0}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["queueIndex"], 0)

        # prev_item on the first item: no-op, no broadcast.
        await control.send_json_to({"type": "command", "command": {"action": "prev_item"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        # next_item on the last item: no-op, no broadcast.
        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 1}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["queueIndex"], 1)
        await control.send_json_to({"type": "command", "command": {"action": "next_item"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": False}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertFalse(msg["blank"])

        # The pointer never moved.
        probe = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        connected, _ = await probe.connect()
        self.assertTrue(connected)
        msg = await probe.receive_json_from()
        self.assertEqual(msg["state"]["queueIndex"], 1)
        await probe.disconnect()
        await display.disconnect()
        await control.disconnect()

    async def test_queue_single_slide_item_nav_is_noop(self):
        # Announcement and countdown are single-slide items: plain next/prev
        # must never cross into another service item.
        room = await sync_to_async(Room.objects.create)(code=ROOM_CODE)
        ann = await sync_to_async(Announcement.objects.create)(title="Notice", body="Hello")
        await sync_to_async(QueueItem.objects.create)(room=room, order=0, item_type="announcement", ref_id=ann.id, ref_data="{}")
        await sync_to_async(QueueItem.objects.create)(
            room=room, order=1, item_type="countdown", ref_id=None,
            ref_data='{"seconds": 300, "title": "Break"}',
        )
        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        control = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        await display.connect()
        await control.connect()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 0}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["contentType"], "announcement")
        self.assertEqual(msg["state"]["slideCount"], 1)

        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        # The countdown is single-slide too; next/prev are no-ops, but the
        # explicit item nav still moves the service along.
        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 1}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["contentType"], "countdown")
        self.assertEqual(msg["state"]["slideCount"], 1)
        await control.send_json_to({"type": "command", "command": {"action": "prev"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": False}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertFalse(msg["blank"])
        await display.disconnect()
        await control.disconnect()

    async def test_adhoc_scripture_keeps_queue_pointer(self):
        song_ids, display, control = await self._open_queued_songs(2)
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {"action": "goto_queue", "index": 0}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["state"]["queueIndex"], 0)

        # Present scripture directly (ad-hoc): pointer untouched, itemId null.
        await control.send_json_to({"type": "command", "command": {
            "action": "present_scripture", "book": "John", "chapter": 3,
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["contentType"], "scripture")
        self.assertEqual(msg["state"]["itemId"], None)
        self.assertEqual(msg["state"]["queueIndex"], 0)

        # Plain next at the last verse must not cross into a queue item.
        await control.send_json_to({"type": "command", "command": {"action": "goto_slide", "slideIndex": 35}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "slide_change")
        await control.send_json_to({"type": "command", "command": {"action": "next"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        # Item nav still advances the pointer: next item becomes item 2.
        await control.send_json_to({"type": "command", "command": {"action": "next_item"}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["queueIndex"], 1)
        self.assertEqual(msg["state"]["itemId"], song_ids[1])
        self.assertEqual(msg["state"]["contentType"], "song")
        self.assertEqual(msg["state"]["slideIndex"], 0)
        await display.disconnect()
        await control.disconnect()

    async def test_item_nav_without_queue_pointer_is_noop(self):
        song_id, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {"action": "present_song", "id": song_id}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["queueIndex"], -1)

        await control.send_json_to({"type": "command", "command": {"action": "next_item"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        await control.send_json_to({"type": "command", "command": {"action": "prev_item"}})
        await control.send_json_to({"type": "command", "command": {"action": "blank", "blank": False}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertFalse(msg["blank"])
        await display.disconnect()
        await control.disconnect()

    def _make_image(self, fit):
        """Upload a tiny image the same way views does and return its id.
        (Sync helper; call via sync_to_async from async tests.)"""
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        import uuid as _uuid

        buf = io.BytesIO()
        Image.new("RGB", (600, 400), (30, 90, 160)).save(buf, format="JPEG")
        result = process_image(buf.getvalue(), "wall.jpg")
        stem = _uuid.uuid4().hex
        for kind, blob in (
            ("thumb", result["variants"]["thumb"]),
            ("soft", result["variants"]["soft"]),
            ("strong", result["variants"]["strong"]),
        ):
            default_storage.save("images/%s.%s.jpg" % (stem, kind), ContentFile(blob))
        img = ImageItem(title="Wall", fit=fit, sha256=result["sha256"])
        img.file.save("images/%s.jpg" % stem, ContentFile(result["main"]), save=True)
        return img.id

    async def test_present_image_state_carries_fit_and_backdrop(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()
        img_id = await sync_to_async(self._make_image)("cover")

        await control.send_json_to({"type": "command", "command": {"action": "present_image", "id": img_id}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "state")
        self.assertEqual(msg["state"]["contentType"], "image")
        self.assertEqual(msg["state"]["content"]["fit"], "cover")
        # The backdrop is the pre-blurred strong variant, not the main file.
        self.assertIn(".strong.jpg", msg["state"]["content"]["backdrop"])
        self.assertNotIn(".strong.jpg", msg["state"]["content"]["url"])
        self.assertEqual(msg["state"]["slideCount"], 1)
        await display.disconnect()
        await control.disconnect()

    async def test_set_style_image_blur_whitelist(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await control.send_json_to({"type": "command", "command": {
            "action": "set_style",
            "styles": {"background": {"type": "image", "value": "/media/wall.jpg", "blur": "strong"}},
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "style_change")
        bg = msg["styles"]["background"]
        self.assertEqual(bg["type"], "image")
        self.assertEqual(bg["value"], "/media/wall.jpg")
        self.assertEqual(bg["blur"], "strong")

        # Anything outside off/soft/strong falls back to "off".
        await control.send_json_to({"type": "command", "command": {
            "action": "set_style",
            "styles": {"background": {"type": "image", "value": "/media/wall.jpg", "blur": "wild"}},
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["styles"]["background"]["blur"], "off")
        self.assertEqual(msg["styles"]["background"]["value"], "/media/wall.jpg")

        await control.send_json_to({"type": "command", "command": {
            "action": "set_style",
            "styles": {"background": {"type": "image", "value": "/media/wall.jpg", "blur": "soft"}},
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["styles"]["background"]["blur"], "soft")
        await display.disconnect()
        await control.disconnect()

    async def _send_style(self, display, control, styles):
        await control.send_json_to({"type": "command", "command": {
            "action": "set_style", "styles": styles,
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "style_change")
        await control.receive_json_from()
        return msg

    async def test_set_style_text_fields(self):
        """New text fields travel in ONE style_change message, validated."""
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        msg = await self._send_style(display, control, {
            "font": "noto-serif",
            "size": 1.25,
            "textColor": "#FFD700",
            "referenceColor": "#FFF7E6",
            "bold": True,
            "lineSpacing": "loose",
            "align": "left",
            "shadow": "strong",
        })
        st = msg["styles"]
        self.assertEqual(st["font"], "noto-serif")
        self.assertEqual(st["size"], 1.25)
        self.assertEqual(st["textColor"], "#ffd700")
        self.assertEqual(st["referenceColor"], "#fff7e6")
        self.assertTrue(st["bold"])
        self.assertEqual(st["lineSpacing"], "loose")
        self.assertEqual(st["align"], "left")
        self.assertEqual(st["shadow"], "strong")
        await display.disconnect()
        await control.disconnect()

    async def test_set_style_accepts_every_kept_font(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()
        for font in ("default", "serif", "sans", "noto-sans", "noto-serif", "montserrat", "merriweather"):
            msg = await self._send_style(display, control, {"font": font})
            self.assertEqual(msg["styles"]["font"], font)
        # unknown fonts are rejected silently
        msg = await self._send_style(display, control, {"font": "comic-sans"})
        self.assertEqual(msg["styles"]["font"], "merriweather")
        await display.disconnect()
        await control.disconnect()

    async def test_set_style_invalid_text_values_ignored(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        msg = await self._send_style(display, control, {"textColor": "#AABBCC", "size": 0.8})
        self.assertEqual(msg["styles"]["textColor"], "#aabbcc")
        self.assertEqual(msg["styles"]["size"], 0.8)

        invalid = (
            {"textColor": "red"},
            {"textColor": "#fff"},
            {"textColor": "#ggg"},
            {"textColor": "javascript:alert(1)"},
            {"textColor": "#1234567"},
            {"referenceColor": "#12345"},
            {"bold": "yes"},
            {"lineSpacing": "ultra"},
            {"align": "right"},
            {"shadow": "glow"},
            {"size": 2.5},
            {"size": "huge"},
        )
        for styles in invalid:
            msg = await self._send_style(display, control, styles)
            st = msg["styles"]
            self.assertEqual(st["font"], "default")  # prior valid values survive
            self.assertEqual(st["textColor"], "#aabbcc")
            self.assertEqual(st["size"], 0.8)
            self.assertNotIn("referenceColor", st)
            self.assertNotIn("bold", st)
            self.assertNotIn("lineSpacing", st)
            self.assertNotIn("align", st)
            self.assertNotIn("shadow", st)
        await display.disconnect()
        await control.disconnect()

    async def test_set_style_size_boundaries_rounding(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        msg = await self._send_style(display, control, {"size": 1.33})
        self.assertEqual(msg["styles"]["size"], 1.35)
        msg = await self._send_style(display, control, {"size": 1.6})
        self.assertEqual(msg["styles"]["size"], 1.6)
        msg = await self._send_style(display, control, {"size": 0.6})
        self.assertEqual(msg["styles"]["size"], 0.6)
        msg = await self._send_style(display, control, {"size": "md"})
        self.assertEqual(msg["styles"]["size"], "md")
        await display.disconnect()
        await control.disconnect()

    async def test_set_style_null_clears_optional_fields(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        await self._send_style(display, control, {"textColor": "#112233", "bold": True})
        msg = await self._send_style(display, control, {"textColor": None, "bold": None})
        st = msg["styles"]
        self.assertNotIn("textColor", st)
        self.assertNotIn("bold", st)
        # font / size / background (in DEFAULT_STYLES) can never be "cleared"
        self.assertEqual(st["font"], "default")
        self.assertEqual(st["size"], "md")
        await display.disconnect()
        await control.disconnect()

    async def test_set_style_legacy_values_still_work(self):
        _, display, control = await self._open_rooms()
        await display.receive_json_from()
        await control.receive_json_from()

        msg = await self._send_style(display, control, {"font": "serif", "size": "lg"})
        self.assertEqual(msg["styles"]["font"], "serif")
        self.assertEqual(msg["styles"]["size"], "lg")
        await display.disconnect()
        await control.disconnect()

    async def test_new_room_styles_match_today_defaults(self):
        _, display, control = await self._open_rooms()
        init = await display.receive_json_from()
        st = init["state"]["styles"]
        self.assertEqual(st.get("font"), "default")
        self.assertEqual(st.get("size"), "md")
        self.assertEqual(st.get("background"), {"type": "color", "value": "#05070A"})
        # Optional text fields are ABSENT so the CSS built-in fallbacks keep
        # today's exact look until the operator changes something.
        for field in ("textColor", "referenceColor", "bold", "lineSpacing", "align", "shadow"):
            self.assertNotIn(field, st)
        await control.receive_json_from()
        await display.disconnect()
        await control.disconnect()

# ============================================================================
# Transparent / chroma display modes
# ============================================================================

class TransparentBackgroundStyleTests(django.test.TransactionTestCase):
    """The two compositing frame modes travel over the existing style message.

    No new WebSocket message type is introduced and no extra round trip is
    added: a display already understands `style_change`, so a transparent wall
    costs the congregation's phone nothing.
    """

    async def _open(self):
        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        control = WebsocketCommunicator(application, f"/ws/control/{ROOM_CODE}/")
        self.assertTrue((await display.connect())[0])
        self.assertTrue((await control.connect())[0])
        await display.receive_json_from()
        await control.receive_json_from()
        return display, control

    async def _style(self, display, control, styles):
        await control.send_json_to({"type": "command", "command": {
            "action": "set_style", "styles": styles,
        }})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "style_change")
        return msg["styles"]

    async def test_transparent_carries_no_value(self):
        display, control = await self._open()
        st = await self._style(display, control, {"background": {"type": "transparent"}})
        # Exactly {"type": "transparent"} — a stale colour or blur must not be
        # able to leak into a frame that is supposed to be see-through.
        self.assertEqual(st["background"], {"type": "transparent"})
        await display.disconnect()
        await control.disconnect()

    async def test_transparent_drops_a_stale_blur(self):
        """A room switched image -> transparent must not keep 'blur'."""
        display, control = await self._open()
        await self._style(display, control, {
            "background": {"type": "image", "value": "/media/wall.jpg", "blur": "strong"},
        })
        st = await self._style(display, control, {"background": {"type": "transparent"}})
        self.assertNotIn("blur", st["background"])
        await display.disconnect()
        await control.disconnect()

    async def test_chroma_keeps_a_valid_key_colour(self):
        display, control = await self._open()
        st = await self._style(display, control, {
            "background": {"type": "chroma", "value": "#00B140"},
        })
        self.assertEqual(st["background"], {"type": "chroma", "value": "#00b140"})
        await display.disconnect()
        await control.disconnect()

    async def test_chroma_falls_back_to_green_on_junk(self):
        """A bad key colour must never be able to paint the wall unreadable."""
        display, control = await self._open()
        for bad in ("red", "#fff", "#gggggg", "javascript:alert(1)", "#00FF00; background:url(x)", None, 42):
            st = await self._style(display, control, {
                "background": {"type": "chroma", "value": bad},
            })
            self.assertEqual(st["background"], {"type": "chroma", "value": "#00ff00"},
                             msg=f"value={bad!r}")
        await display.disconnect()
        await control.disconnect()

    async def test_mode_round_trip_back_to_normal(self):
        """Normal mode must be byte-for-byte what it was before these modes."""
        display, control = await self._open()
        await self._style(display, control, {"background": {"type": "transparent"}})
        st = await self._style(display, control, {"background": {"type": "color", "value": "#1E2A38"}})
        self.assertEqual(st["background"], {"type": "color", "value": "#1E2A38"})
        await display.disconnect()
        await control.disconnect()

    async def test_unknown_background_type_is_ignored(self):
        display, control = await self._open()
        await self._style(display, control, {"background": {"type": "color", "value": "#000000"}})
        st = await self._style(display, control, {"background": {"type": "hologram", "value": "#000000"}})
        self.assertEqual(st["background"], {"type": "color", "value": "#000000"})
        await display.disconnect()
        await control.disconnect()


class NormalizeBackgroundTests(django.test.SimpleTestCase):
    """Unit-level checks on the validator, independent of the socket layer."""

    def test_image_behaviour_is_unchanged(self):
        from .consumer import normalize_background
        self.assertEqual(
            normalize_background({"type": "image", "value": "/media/a.jpg", "blur": "soft"}),
            {"type": "image", "value": "/media/a.jpg", "blur": "soft"},
        )
        self.assertEqual(normalize_background({"type": "image", "value": "/media/a.jpg"}),
                         {"type": "image", "value": "/media/a.jpg", "blur": "off"})
        self.assertEqual(normalize_background({"type": "image", "value": "/media/a.jpg", "blur": "wild"}),
                         {"type": "image", "value": "/media/a.jpg", "blur": "off"})
        self.assertEqual(normalize_background({"type": "color", "value": "#FFFFFF"}),
                         {"type": "color", "value": "#FFFFFF"})

    def test_value_is_required_for_colour_and_image(self):
        from .consumer import normalize_background
        # No value means "leave the current background alone" (returns None).
        self.assertIsNone(normalize_background({"type": "image"}))
        self.assertIsNone(normalize_background({"type": "color", "value": ""}))
        self.assertIsNone(normalize_background("not a dict"))
        self.assertIsNone(normalize_background(None))

    def test_transparent_ignores_any_value(self):
        from .consumer import normalize_background
        self.assertEqual(
            normalize_background({"type": "transparent", "value": "#FF0000", "blur": "strong"}),
            {"type": "transparent"},
        )


class DisplayBackgroundOverrideTests(django.test.TestCase):
    """?bg= pins one browser's frame without touching the room's style."""

    def test_override_is_passed_to_the_template(self):
        for choice in ("transparent", "chroma"):
            with self.subTest(bg=choice):
                r = self.client.get(f"/display/{ROOM_CODE}?bg={choice}")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.context["display_bg"], choice)

    def test_default_and_junk_fall_back_to_room(self):
        r = self.client.get(f"/display/{ROOM_CODE}")
        self.assertEqual(r.context["display_bg"], "room")
        # An unknown value must not reach display.js as anything it would honour.
        for junk in ("banana", "javascript:alert(1)", "translucent", "0"):
            with self.subTest(bg=junk):
                r = self.client.get(f"/display/{ROOM_CODE}", {"bg": junk})
                self.assertEqual(r.context["display_bg"], "room")

    def test_case_and_whitespace_are_tolerated(self):
        """These URLs get typed and edited by hand; be forgiving about both."""
        for variant in ("TRANSPARENT", "  chroma  ", "Chroma", "Transparent"):
            with self.subTest(bg=variant):
                r = self.client.get(f"/display/{ROOM_CODE}", {"bg": variant})
                self.assertEqual(r.context["display_bg"], variant.strip().lower())

    def test_explicit_room_is_valid(self):
        r = self.client.get(f"/display/{ROOM_CODE}?bg=room")
        self.assertEqual(r.context["display_bg"], "room")

    def test_override_is_reachable_from_the_console(self):
        """The view must not create a second Room row for a bogus code path."""
        r = self.client.get("/display/NEWROOM01?bg=transparent")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Room.objects.filter(code="NEWROOM01").exists())


# ============================================================================
# Controller route + presence
# ============================================================================

class ControllerRouteTests(django.test.TransactionTestCase):
    """/ws/control/ is additive: same room group, same protocol, but it is
    recorded as a controller so the Connect panel can report a live phone."""

    async def test_control_route_speaks_the_same_protocol(self):
        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        control = WebsocketCommunicator(application, f"/ws/control/{ROOM_CODE}/")
        self.assertTrue((await display.connect())[0])
        self.assertTrue((await control.connect())[0])

        init = await control.receive_json_from()
        self.assertEqual(init["type"], "state")
        self.assertEqual(init["state"]["room"], ROOM_CODE)

        # A blank from the controller must reach the display untouched: the
        # route changed, the protocol did not.
        await display.receive_json_from()
        await control.send_json_to({"type": "command", "command": {"action": "blank", "value": True}})
        msg = await display.receive_json_from()
        self.assertEqual(msg["type"], "blank")
        self.assertTrue(msg["blank"])

        await display.disconnect()
        await control.disconnect()

    async def test_display_route_still_exists_and_is_not_counted_as_a_phone(self):
        from .consumer import presence
        display = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}/")
        self.assertTrue((await display.connect())[0])
        await display.receive_json_from()
        # A projector must never make the Connect panel claim a phone is here.
        self.assertEqual(presence(ROOM_CODE)["control"], 0)
        self.assertEqual(presence(ROOM_CODE)["display"], 1)
        await display.disconnect()

    async def test_presence_counts_controllers_and_drops_them_on_disconnect(self):
        from .consumer import presence, rooms_with_controller
        control = WebsocketCommunicator(application, f"/ws/control/{ROOM_CODE}/")
        self.assertTrue((await control.connect())[0])
        await control.receive_json_from()
        self.assertEqual(presence(ROOM_CODE)["control"], 1)
        self.assertIn(ROOM_CODE, rooms_with_controller())

        await control.disconnect()
        # disconnect() only queues the close frame; receive_output lets the
        # application finish handling it.
        for _ in range(4):
            try:
                await control.receive_output(timeout=1)
            except Exception:
                break
        self.assertEqual(presence(ROOM_CODE)["control"], 0)
        self.assertNotIn(ROOM_CODE, rooms_with_controller())

    async def test_route_requires_the_trailing_slash(self):
        """A missing trailing slash must be rejected cleanly, not crash.

        control.js builds the path itself, so a regression here would show up
        as a phone that silently never connects. Over Daphne the unhandled
        router exception surfaced as HTTP 500 plus a traceback — an internal
        server error for what is really a typo'd URL. The connection must now
        simply refuse to open, and it must not raise in the harness either.
        """
        bad = WebsocketCommunicator(application, f"/ws/control/{ROOM_CODE}")
        opened, detail = await bad.connect()
        self.assertFalse(opened, "missing-slash URL must not open a socket")

    async def test_display_route_also_requires_the_trailing_slash(self):
        """Same contract for the display route, for the same reason."""
        bad = WebsocketCommunicator(application, f"/ws/room/{ROOM_CODE}")
        opened, detail = await bad.connect()
        self.assertFalse(opened, "missing-slash URL must not open a socket")

    async def test_valid_slash_routes_still_open(self):
        """The two real routes are untouched by the rejection wrapper."""
        for path in (
            f"/ws/control/{ROOM_CODE}/",
            f"/ws/room/{ROOM_CODE}/",
        ):
            comm = WebsocketCommunicator(application, path)
            opened, _ = await comm.connect()
            self.assertTrue(opened, f"{path} must still connect")
            await comm.receive_json_from()
            await comm.disconnect()


class PresenceApiTests(django.test.TestCase):
    def test_unknown_room_reports_zero_rather_than_erroring(self):
        r = self.client.get("/api/rooms/NOSUCH/presence")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["code"], "NOSUCH")
        self.assertFalse(body["connected"])
        self.assertEqual(body["control"], 0)

    def test_connected_flag_reflects_controllers_only(self):
        from .consumer import presence
        Room.objects.get_or_create(code=ROOM_CODE)
        try:
            presence(ROOM_CODE)  # touch to ensure the entry exists
            r = self.client.get(f"/api/rooms/{ROOM_CODE}/presence")
            self.assertFalse(r.json()["connected"])
        finally:
            pass


# ============================================================================
# Local network discovery + QR
# ============================================================================

class ServerPortTests(django.test.SimpleTestCase):
    """The QR code must encode the port the server is really listening on."""

    def _report(self, meta_port=None, env=None):
        from presentation import netinfo
        old = os.environ.get("PROCLAIM_PORT")
        if env is None:
            os.environ.pop("PROCLAIM_PORT", None)
        else:
            os.environ["PROCLAIM_PORT"] = env
        try:
            req = django.test.RequestFactory().get("/api/network")
            if meta_port is not None:
                req.META["SERVER_PORT"] = str(meta_port)
            else:
                # RequestFactory defaults SERVER_PORT to 80, which is a truthful
                # value; remove it so the fallback chain is actually exercised.
                req.META.pop("SERVER_PORT", None)
            return netinfo.server_port(req)
        finally:
            if old is None:
                os.environ.pop("PROCLAIM_PORT", None)
            else:
                os.environ["PROCLAIM_PORT"] = old

    def test_request_port_wins_over_the_environment(self):
        # A reverse proxy or an unusual bind can disagree with PROCLAIM_PORT;
        # what the phone will actually dial is the port in the request.
        self.assertEqual(self._report(meta_port=4123, env="3000"), 4123)

    def test_falls_back_to_the_environment(self):
        self.assertEqual(self._report(meta_port=None, env="4000"), 4000)

    def test_default_when_nothing_is_known(self):
        self.assertEqual(self._report(meta_port=None, env=None), 3000)

    def test_junk_is_ignored(self):
        self.assertEqual(self._report(meta_port=None, env="not-a-port"), 3000)


class ConnectionOptionsTests(django.test.SimpleTestCase):
    """Ranking rules, tested against a synthetic interface table.

    The point of the ranking is that a Docker/VirtualBox address is often listed
    first by the OS but is unreachable from a phone, so it must never win.
    """

    def setUp(self):
        from presentation import netinfo
        self.netinfo = netinfo

    def _entry(self, ip, interface, kind, label, likely, route=False):
        """One entry shaped exactly like netinfo.enumerate_addresses() returns."""
        return {"ip": ip, "interface": interface, "kind": kind, "label": label,
                "likely": likely, "private": True, "route": route}

    def _options(self, entries, port=3000):
        orig = self.netinfo.enumerate_addresses
        self.netinfo.enumerate_addresses = lambda: entries
        try:
            return self.netinfo.connection_options(port)
        finally:
            self.netinfo.enumerate_addresses = orig

    def test_virtual_interfaces_are_demoted_but_still_shown(self):
        entries = [
            self._entry("172.17.0.1", "docker0", "virtual",
                        "Virtual adapter (phone will not reach this)", False),
            self._entry("192.168.1.50", "wlp58s0", "wifi", "Wi-Fi", True, route=True),
        ]
        options = [o for o in self._options(entries) if o["kind"] != "hostname"]
        self.assertEqual(len(options), 2, "nothing may be silently discarded")
        self.assertFalse(options[0]["likely"])
        self.assertTrue(options[1]["likely"])
        # The operator has to be able to tell, at a glance, why an address is
        # listed last — otherwise they will pick it and wonder why it fails.
        self.assertIn("Virtual adapter", options[0]["label"])

    def test_urls_carry_the_requested_port(self):
        entries = [self._entry("10.0.0.7", "eth0", "ethernet", "Ethernet", True)]
        self.assertEqual(self._options(entries, port=4001)[0]["url"], "http://10.0.0.7:4001/")

    def test_primary_prefers_the_likely_interface(self):
        entries = [
            self._entry("172.18.0.1", "br-abc", "virtual",
                        "Virtual adapter (phone will not reach this)", False),
            self._entry("192.168.137.2", "wlan0", "wifi", "Wi-Fi", True),
        ]
        orig_enum = self.netinfo.enumerate_addresses
        orig_name = self.netinfo.hostname
        self.netinfo.enumerate_addresses = lambda: entries
        self.netinfo.hostname = lambda: ""
        try:
            self.assertEqual(self.netinfo.primary_url(3000), "http://192.168.137.2:3000/")
        finally:
            self.netinfo.enumerate_addresses = orig_enum
            self.netinfo.hostname = orig_name

    def test_hostname_never_wins_the_qr_code(self):
        """mDNS is unreliable and must not be what the phone is asked to open."""
        entries = [self._entry("192.168.137.2", "wlan0", "wifi", "Wi-Fi", False)]
        orig_enum = self.netinfo.enumerate_addresses
        orig_name = self.netinfo.hostname
        self.netinfo.enumerate_addresses = lambda: entries
        self.netinfo.hostname = lambda: "laptop"
        try:
            self.assertEqual(self.netinfo.primary_url(3000), "http://192.168.137.2:3000/")
        finally:
            self.netinfo.enumerate_addresses = orig_enum
            self.netinfo.hostname = orig_name

    def test_no_candidates_still_yields_a_usable_url(self):
        orig_enum = self.netinfo.enumerate_addresses
        orig_name = self.netinfo.hostname
        self.netinfo.enumerate_addresses = lambda: []
        self.netinfo.hostname = lambda: ""
        try:
            self.assertEqual(self.netinfo.primary_url(3000), "http://127.0.0.1:3000/")
        finally:
            self.netinfo.enumerate_addresses = orig_enum
            self.netinfo.hostname = orig_name

    def test_unusable_addresses_are_filtered_out_entirely(self):
        """Loopback, unspecified and link-local can never be typed on a phone."""
        import socket as _socket
        for addr in ("127.0.0.1", "0.0.0.0", "169.254.10.1", "::1"):
            with self.subTest(addr=addr):
                self.assertTrue(
                    addr.startswith(("127.", "0.0.0.0", "169.254.", "::")) and not addr.startswith("192.168."),
                    "sanity: the filter list matches the documented exclusions",
                )
        # The real function, on the real machine, must never return these.
        from presentation.netinfo import enumerate_addresses
        for entry in enumerate_addresses():
            ip = entry["ip"]
            self.assertFalse(ip.startswith("127."), ip)
            self.assertFalse(ip.startswith("169.254."), ip)
            self.assertNotEqual(ip, "0.0.0.0", ip)
            _socket.inet_aton(ip)  # must be a parseable address


class QrCodeTests(django.test.SimpleTestCase):
    def test_qr_is_a_png(self):
        from presentation import netinfo
        png = netinfo.qr_png_bytes("http://192.168.1.50:3000/")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        # And it really is a decodable-sized QR, not a 1x1 placeholder.
        img = Image.open(io.BytesIO(png))
        self.assertGreaterEqual(img.width, 100)
        self.assertEqual(img.width, img.height)

    def test_data_uri_is_inline_png(self):
        from presentation import netinfo
        uri = netinfo.qr_data_uri("http://192.168.1.50:3000/")
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        import base64
        raw = base64.b64decode(uri.split(",", 1)[1])
        self.assertTrue(raw.startswith(b"\x89PNG"))

    def test_long_hotspot_urls_still_fit(self):
        """192.168.x.x hosts plus a long path must not overflow the symbol."""
        from presentation import netinfo
        long_url = "http://192.168.137.123:65535/display/" + "R" * 200
        self.assertTrue(netinfo.qr_png_bytes(long_url).startswith(b"\x89PNG"))


class NetworkApiTests(django.test.TestCase):
    def test_network_info_shape(self):
        Room.objects.get_or_create(code=ROOM_CODE)
        r = self.client.get("/api/network")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("port", "bind", "hostname", "primaryUrl", "urls", "firewallHint", "netshCommand", "rooms", "presence"):
            self.assertIn(key, body, key)
        self.assertEqual(body["bind"], "0.0.0.0")
        self.assertTrue(body["primaryUrl"].startswith("http://"))
        self.assertIn(ROOM_CODE, [room["code"] for room in body["rooms"]])

    def test_netsh_command_contains_the_real_port(self):
        """Regression: the command used to ship a literal %d."""
        r = self.client.get("/api/network")
        self.assertNotIn("%d", r.json()["netshCommand"])
        self.assertIn(str(r.json()["port"]), r.json()["netshCommand"])

    def test_urls_are_all_local_addresses(self):
        """Nothing in this payload may reference a public host."""
        body = self.client.get("/api/network").json()
        for option in body["urls"]:
            host = option["url"].split("//", 1)[1].split(":", 1)[0]
            self.assertNotIn("://", host)
            self.assertTrue(
                host.startswith(("127.", "10.", "172.", "192.168.")) or host.endswith(".local"),
                msg=f"{host} is not a local address",
            )

    def test_qr_endpoint_returns_a_png(self):
        r = self.client.get("/api/network/qr")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        # A local address that can change must never be cached.
        self.assertIn("no-store", r["Cache-Control"])
        self.assertTrue(r["X-Proclaim-Url"].startswith("http://"))

    def test_qr_index_is_clamped_not_a_500(self):
        for raw in ("0", "1", "999", "-3", "abc", "", "1.5"):
            with self.subTest(index=raw):
                r = self.client.get("/api/network/qr", {"index": raw})
                self.assertEqual(r.status_code, 200)
                self.assertTrue(r.content.startswith(b"\x89PNG"))

    def test_qr_encodes_the_same_url_the_report_advertises(self):
        """The code and the written address must never disagree."""
        report = self.client.get("/api/network").json()
        r = self.client.get("/api/network/qr")
        self.assertEqual(r["X-Proclaim-Url"], report["primaryUrl"])
        self.assertIn(f":{report['port']}/", r["X-Proclaim-Url"])

    def test_qr_index_selects_a_specific_address(self):
        report = self.client.get("/api/network").json()
        if len(report["urls"]) < 2:
            self.skipTest("this machine has only one candidate address")
        second = report["urls"][1]["url"]
        r = self.client.get("/api/network/qr", {"index": 1})
        self.assertEqual(r["X-Proclaim-Url"], second)


# ============================================================================
# Typography / asset wiring
# ============================================================================

class StaticAssetMixin:
    """Read bundled assets off disk.

    The test runner does not serve /static/ (that is a DEBUG-only, live-server
    behaviour), and the packaged build reads these same files out of
    _internal/. Going through the staticfiles finder therefore matches both.
    """

    def asset(self, logical_path):
        """`logical_path` is the STATIC-relative path, e.g. presentation/js/x.js."""
        found = finders.find(logical_path)
        self.assertIsNotNone(found, f"{logical_path} is not bundled as a static file")
        with open(found, encoding="utf-8") as fh:
            return fh.read()


class PresentationTypographyTests(StaticAssetMixin, django.test.TestCase):
    """One projected type scale, shared by the wall and the phone preview."""

    def test_design_system_exists(self):
        css = self.asset("presentation/css/presentation.css")
        for selector in (".d-slide", ".d-slide__title", ".d-slide__body",
                         ".d-slide__label", ".d-slide__timer", "--pres-scale"):
            self.assertIn(selector, css, selector)

    def test_type_scale_is_relative_so_it_scales_with_the_wall(self):
        """em-based sizing is what lets one rule serve a laptop and a projector.

        The single knob is --pres-em (the display sets it from the viewport, the
        thumbnail from a fixed 15px). --pres-scale is a unitless multiplier on
        top of that, and every role size is expressed in em against it — so
        changing one number rescales the whole wall coherently.
        """
        css = self.asset("presentation/css/presentation.css")
        lines = css.splitlines()

        self.assertTrue([ln for ln in lines if "--pres-em:" in ln],
                        "--pres-em is not declared")

        # --pres-scale is the unitless operator-size x shrink-to-fit multiplier;
        # the two meet on .d-slide's own font-size. Check that seam, because it
        # is the one place a hard-coded px would silently kill the whole system.
        slide = self._block(css, ".d-slide")
        self.assertRegex(slide, r"font-size:\s*calc\(var\(--pres-em\)\s*\*\s*var\(--pres-scale\)\)", slide)

        # Roles that deliberately sit *below* the slide's own size must express
        # that as a ratio, never as an absolute value.
        for role in ("__title", "__body", "__label", "__timer", "__ref", "__note"):
            block = self._block(css, f".d-slide{role}")
            sizes = re.findall(r"font-size:\s*([^;]+);", block)
            self.assertTrue(sizes, f".d-slide{role} has no font-size")
            for size in sizes:
                self.assertIn("em", size, f".d-slide{role} is not relative: {size}")

        # Scripture verse text and song lyrics ARE the main wall text, so they
        # intentionally inherit .d-slide's size rather than restating it. Assert
        # that they declare no font-size at all — a stray 1em here would be
        # harmless today but would break the moment the base scale moved.
        for role in ("__verse", "__lyric"):
            block = self._block(css, f".d-slide{role}")
            self.assertNotIn("font-size", block,
                             f".d-slide{role} should inherit the slide size")
            # They still take their colour and weight from the tokens.
            self.assertIn("var(--d-weight", block + self._block(css, ".d-slide"),
                          f".d-slide{role} lost its weight token")

        # A wall that is a hair narrower must not re-declare the whole scale.
        self.assertNotIn("px", self._block(css, ".d-slide"))

    @staticmethod
    def _block(css, selector):
        """The declaration block for exactly `selector`, or ''."""
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        return match.group(1) if match else ""

    def test_display_and_control_both_load_it(self):
        for path in (f"/display/{ROOM_CODE}", "/control"):
            with self.subTest(page=path):
                html = self.client.get(path).content.decode()
                self.assertIn("presentation/css/presentation.css", html)

    def test_display_publishes_its_background_override(self):
        html = self.client.get(f"/display/{ROOM_CODE}?bg=transparent").content.decode()
        self.assertIn("window.DISPLAY_BG", html)
        self.assertIn("transparent", html)


class TransparentSurfaceTests(StaticAssetMixin, django.test.TestCase):
    """The frame must not be able to paint an opaque surface of its own."""

    def test_display_css_routes_every_surface_through_a_variable(self):
        css = self.asset("presentation/css/display.css")
        for var in ("--d-page-bg", "--d-blank-bg", "--d-figure-bg"):
            self.assertIn(var, css, var)
        self.assertIn("var(--d-page-bg)", css)
        # The letterbox behind a projected image is the classic place where an
        # opaque black box leaks over the video underneath.
        self.assertRegex(css, r"\.d-image\s*\{[^}]*background:\s*var\(--d-figure-bg\)")

    def test_transparent_class_clears_display_and_stage(self):
        css = self.asset("presentation/css/display.css")
        self.assertIn(".d-transparent #display", css)
        self.assertIn("transparent", css)

    def test_controller_preview_shows_a_checkerboard(self):
        """Transparent mode must be distinguishable from plain black."""
        css = self.asset("presentation/css/app.css")
        block = re.search(r"\.cc-transparent\s*\{[^}]*\}", css)
        self.assertIsNotNone(block, ".cc-transparent rule is missing")
        body = block.group(0)
        # The classic two-offset-gradient checkerboard, not a flat colour.
        self.assertGreaterEqual(body.count("linear-gradient"), 2, body)
        self.assertIn("background-size", body, body)


class ConnectAssetsTests(StaticAssetMixin, django.test.TestCase):
    def test_connect_assets_are_bundled(self):
        for path in ("presentation/css/net.css", "presentation/js/net.js"):
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), path)

    def test_connect_panel_never_calls_an_external_host(self):
        """The QR code must be generated by this machine, not fetched."""
        js = self.asset("presentation/js/net.js")
        self.assertNotIn("http://", js.replace("ms-settings:", ""))
        self.assertNotIn("https://", js)

    def test_landing_page_loads_them_and_shows_the_qr(self):
        html = self.client.get("/").content.decode()
        self.assertIn("presentation/css/net.css", html)
        self.assertIn("presentation/js/net.js", html)
        self.assertIn("/api/network/qr", html)

    def test_net_js_only_touches_elements_the_page_has(self):
        """A typo'd getElementById is a silent no-op at runtime, not an error."""
        html = self.client.get("/").content.decode()
        ids_in_page = set(re.findall(r'id="([^"]+)"', html))
        wanted = set(re.findall(r'getElementById\("([^"]+)"\)',
                                 self.asset("presentation/js/net.js")))
        self.assertTrue(wanted, "net.js looks up nothing — is it wired up?")
        self.assertEqual(wanted - ids_in_page, set(),
                         "net.js asks for ids the landing page does not render")

    def test_room_rows_carry_the_code_they_report_on(self):
        """net.js refreshes rows via [data-room]; without it they freeze stale."""
        Room.objects.get_or_create(code=ROOM_CODE, defaults={"name": "Test"})
        html = self.client.get("/").content.decode()
        self.assertIn(f'data-room="{ROOM_CODE}"', html)

    def test_every_element_net_js_paints_is_styled(self):
        css = self.asset("presentation/css/net.css")
        html = self.client.get("/").content.decode()
        js = self.asset("presentation/js/net.js")
        on_page = set(w for group in re.findall(r'class="([^"]+)"', html) for w in group.split())
        on_page |= set(re.findall(r"'(cc-[a-z0-9_-]+)", js))
        styled = set(re.findall(r"\.(cc-[a-z0-9_-]+)", css))
        # Classes the template emits only inside an {% if %}/{% empty %} branch
        # are legitimately absent from this render; check them against the source.
        with open(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "templates/presentation/index.html"), encoding="utf-8") as fh:
            on_page |= set(re.findall(r"cc-[a-z0-9_-]+", fh.read()))
        missing = styled - on_page
        self.assertEqual(missing, set(),
                         f"net.css styles classes nothing uses: {sorted(missing)}")

    def test_landing_page_does_not_leak_wall_only_assets(self):
        """The Connect panel is operator UI; the design system is not for it."""
        html = self.client.get("/").content.decode()
        self.assertNotIn("presentation/css/presentation.css", html)
        self.assertNotIn("presentation/css/display.css", html)

    def test_no_reference_to_removed_typography_classes(self):
        """Old per-slide class names must not survive anywhere."""
        stale = ("d-align", "d-scripture", "d-announcement", "d-countdown", "d-song")
        files = [
            "presentation/css/presentation.css",
            "presentation/css/display.css",
            "presentation/css/app.css",
            "presentation/js/display.js",
            "presentation/js/control.js",
        ]
        for rel in files:
            with self.subTest(file=rel):
                body = self.asset(rel)
                for name in stale:
                    self.assertNotIn(name, body, f"{rel} still references {name}")


class PackagingContractTests(django.test.SimpleTestCase):
    """The portable build must keep working. These are cheap and prevent the
    worst class of regression: a new dependency or asset silently unbundled."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, name):
        with open(os.path.join(self.ROOT, name), encoding="utf-8") as fh:
            return fh.read()

    def test_required_hidden_imports_survive(self):
        spec = self._read("Proclaim.spec")
        for name in ("churchcast.asgi", "presentation.templatetags",
                     "presentation.templatetags.versioned_static", "daphne.cli"):
            self.assertIn(f'"{name}"', spec, name)

    def test_autobahn_nvx_stays_excluded(self):
        """Excluding nvx is what keeps django.setup() alive in the bundle."""
        spec = self._read("Proclaim.spec")
        self.assertIn("autobahn.nvx", spec)
        self.assertIn("_nvx_utf8validator", spec)

    def test_qr_and_psutil_are_bundled(self):
        spec = self._read("Proclaim.spec")
        self.assertIn('"segno"', spec)
        self.assertIn('"psutil"', spec)

    def test_new_dependencies_are_declared_everywhere_they_are_installed(self):
        for script in ("start.sh", "build_windows.bat"):
            body = self._read(script)
            for dep in ("segno", "psutil"):
                self.assertIn(dep, body, f"{dep} missing from {script}")

    def test_portable_banner_shares_the_ranking(self):
        """The console must not advertise a different address than the QR."""
        body = self._read("portable.py")
        self.assertIn("connection_options", body)
        self.assertIn("PROCLAIM_PORT", body)

    def test_banner_advertises_the_ranked_lan_address_not_localhost(self):
        """The bind address is for the server; the printed URL is for the phone.

        A banner showing 127.0.0.1 tells the operator to type an address that
        only works on the machine running Proclaim, so the URL has to come from
        the same ranking the QR code uses.
        """
        import contextlib
        import io

        import portable
        from presentation import netinfo

        entry = {"ip": "192.168.1.178", "interface": "wlp3s0", "kind": "wifi",
                 "label": "Wi-Fi", "likely": True, "private": True, "route": True}
        orig_enum = netinfo.enumerate_addresses
        orig_name = netinfo.hostname
        netinfo.enumerate_addresses = lambda: [entry]
        netinfo.hostname = lambda: ""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                portable.print_banner(3000, False)
        finally:
            netinfo.enumerate_addresses = orig_enum
            netinfo.hostname = orig_name

        out = buf.getvalue()
        self.assertIn("http://192.168.1.178:3000/", out)
        self.assertIn("http://192.168.1.178:3000/control", out)
        self.assertIn("http://192.168.1.178:3000/display/CHURCH1", out)
        self.assertNotIn("127.0.0.1", out)
        # Printing the LAN address must never move the socket it binds to.
        self.assertIn("0.0.0.0", out)

    def test_templates_and_static_whole_trees_are_bundled(self):
        spec = self._read("Proclaim.spec")
        self.assertIn('("presentation/static", "presentation/static")', spec)
        self.assertIn('("presentation/templates", "presentation/templates")', spec)
