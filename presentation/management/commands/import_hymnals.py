"""
Import the two bundled Protestant hymnals into the Song table.

Run once from the terminal:

    .venv/bin/python manage.py import_hymnals [--dir <folder>]

Safe to re-run: hymns are keyed on (source_abbr, number), so repeats never
create duplicates. Verse/chorus bodies are stored byte-for-byte as read from
the JSON (UTF-8), including Yoruba apostrophes (' / ' / '); only the display
title is normalized (see song_import.clean_hymn_title) and the untouched
original is kept in Song.original_title.
"""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from ...models import Song, SongSection
from ...song_import import clean_hymn_title, split_hymn_sections

HYMNAL_SPECS = (
    {
        "file": "ybh(1).json",
        "source_abbr": "YBH",
        "language": "Yoruba",
        "source": "Yoruba Baptist Hymnal",
        "fallback_author": "Nigeria Baptist Convention",
    },
    {
        "file": "nnbh(1).json",
        "source_abbr": "NNBH",
        "language": "English",
        "source": "Baptist Hymnal",
        "fallback_author": "Unknown",
    },
)


def import_hymnal_files(directory):
    """Import every bundled hymnal JSON in `directory`. Returns a report dict.

    Idempotent: a hymn whose (source_abbr, number) already exists is skipped,
    never overwritten. Structurally broken hymns are collected in "failed"
    (with their id + reason) instead of raising.
    """
    report = {
        "imported": 0,
        "imported_ids": [],
        "skipped": 0,
        "failed": [],
    }
    for spec in HYMNAL_SPECS:
        path = os.path.join(directory, spec["file"])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for hymn in data.get("hymns", []):
            _import_one(spec, hymn, report)
    return report


def _import_one(spec, hymn, report):
    hymn_id = hymn.get("id", "")
    metadata = hymn.get("metadata") or {}
    try:
        number = int(metadata.get("number"))
    except (TypeError, ValueError):
        report["failed"].append({"id": hymn_id, "reason": "missing/invalid metadata.number"})
        return

    verses = [v for v in (hymn.get("verses") or []) if str(v).strip()]
    if not verses:
        report["failed"].append({"id": hymn_id, "reason": "no non-empty verses"})
        return

    title_raw = hymn.get("title") or ""
    display_title = clean_hymn_title(title_raw)
    author = str(hymn.get("author") or spec["fallback_author"])

    song, created = Song.objects.get_or_create(
        source_abbr=spec["source_abbr"],
        number=number,
        defaults={
            "title": display_title,
            "original_title": title_raw,
            "author": author,
            "language": spec["language"],
            "source": spec["source"],
        },
    )
    if not created:
        report["skipped"] += 1
        return

    sections = split_hymn_sections(verses, hymn.get("chorus") or "")
    for order, sec in enumerate(sections):
        SongSection.objects.create(
            song=song,
            label=sec["label"],
            lyrics=sec["lyrics"],
            order=order,
        )
    report["imported"] += 1
    report["imported_ids"].append(hymn_id)


def _verify_sample_roundtrip(directory):
    """Compare a sample Yoruba hymn (YBH #1) file text vs what we stored."""
    with open(os.path.join(directory, "ybh(1).json"), encoding="utf-8") as f:
        data = json.load(f)
    source = next(h for h in data["hymns"] if h.get("id") == "ybh_001")
    original = source["verses"][0] if source.get("verses") else ""
    song = Song.objects.filter(source_abbr="YBH", number=1).first()
    stored = song.sections.filter(order=0).first()
    stored_text = stored.lyrics if stored else ""
    return {
        "original": original,
        "stored": stored_text,
        "ok": original == stored_text,
    }


class Command(BaseCommand):
    help = "Import the Yoruba Baptist Hymnal (650) and Baptist Hymnal (325)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default=settings.BASE_DIR,
            help="Directory containing ybh(1).json and nnbh(1).json (default: repo root).",
        )
        parser.add_argument(
            "--num",
            type=int,
            default=None,
            help="Import only this hymn number per hymnal (debugging aid).",
        )

    def handle(self, *args, **opts):
        directory = opts["dir"]
        only = opts.get("num")
        if only:
            for spec in HYMNAL_SPECS:
                report = _import_limited(spec, directory, only)
                _print_report(report)
            return
        report = import_hymnal_files(directory)
        _print_report(report)
        sample = _verify_sample_roundtrip(directory)
        self.stdout.write("\n--- Sample Yoruba round-trip (YBH #1 verse 1) ---")
        self.stdout.write("FILE  : " + sample["original"].replace("\n", " / "))
        self.stdout.write("DB    : " + sample["stored"].replace("\n", " / "))
        self.stdout.write("MATCH : " + ("OK (byte-for-byte)" if sample["ok"] else "MISMATCH"))


def _import_limited(spec, directory, number):
    """Import a single hymn number from one hymnal (used by --num)."""
    path = os.path.join(directory, spec["file"])
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    hymn = next(
        (h for h in data.get("hymns", []) if (h.get("metadata") or {}).get("number") == number),
        None,
    )
    report = {"imported": 0, "imported_ids": [], "skipped": 0, "failed": []}
    if hymn:
        _import_one(spec, hymn, report)
    else:
        report["failed"].append({"id": f"{spec['source_abbr']}#{number}", "reason": "number not found"})
    return report


def _print_report(report):
    print("--- Hymnal import report ---")
    print(f"imported : {report['imported']}")
    print(f"skipped  : {report['skipped']}")
    print(f"failed   : {len(report['failed'])}")
    for item in report["failed"]:
        print(f"  FAILED  {item['id']}: {item['reason']}")