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
import io

import django.test
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from churchcast.asgi import application
from django.conf import settings
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