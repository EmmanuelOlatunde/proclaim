"""
Scripture reference parser and slide builder.

Bundles a small public-domain KJV New Testament excerpt for demonstration.
The full KJV is public domain; data is loaded from a local JSON file.
"""
import json
from pathlib import Path

_BIBLE_CACHE = None

# Book name normalization
_BOOK_ALIASES = {
    "gen": "Genesis", "genesis": "Genesis",
    "ex": "Exodus", "exod": "Exodus", "exodus": "Exodus",
    "lev": "Leviticus", "leviticus": "Leviticus",
    "num": "Numbers", "numbers": "Numbers",
    "deut": "Deuteronomy", "dt": "Deuteronomy", "deuteronomy": "Deuteronomy",
    "josh": "Joshua", "joshua": "Joshua",
    "judg": "Judges", "judges": "Judges",
    "ruth": "Ruth",
    "1sam": "1 Samuel", "1samuel": "1 Samuel", "isam": "1 Samuel",
    "2sam": "2 Samuel", "2samuel": "2 Samuel", "iisam": "2 Samuel",
    "1kgs": "1 Kings", "1kings": "1 Kings", "ikings": "1 Kings",
    "2kgs": "2 Kings", "2kings": "2 Kings", "iikings": "2 Kings",
    "1chr": "1 Chronicles", "1chron": "1 Chronicles", "1chronicles": "1 Chronicles",
    "2chr": "2 Chronicles", "2chron": "2 Chronicles", "2chronicles": "2 Chronicles",
    "ezra": "Ezra",
    "neh": "Nehemiah", "nehemiah": "Nehemiah",
    "est": "Esther", "esther": "Esther",
    "job": "Job",
    "ps": "Psalms", "psa": "Psalms", "psalm": "Psalms", "psalms": "Psalms",
    "prov": "Proverbs", "proverbs": "Proverbs",
    "eccl": "Ecclesiastes", "ecclesiastes": "Ecclesiastes",
    "song": "Song of Solomon", "sos": "Song of Solomon", "songofsolomon": "Song of Solomon",
    "isa": "Isaiah", "isaiah": "Isaiah",
    "jer": "Jeremiah", "jeremiah": "Jeremiah",
    "lam": "Lamentations", "lamentations": "Lamentations",
    "ezek": "Ezekiel", "ezekiel": "Ezekiel",
    "dan": "Daniel", "daniel": "Daniel",
    "hos": "Hosea", "hosea": "Hosea",
    "joel": "Joel",
    "amos": "Amos",
    "obad": "Obadiah", "obadiah": "Obadiah",
    "jonah": "Jonah",
    "mic": "Micah", "micah": "Micah",
    "nah": "Nahum", "nahum": "Nahum",
    "hab": "Habakkuk", "habakkuk": "Habakkuk",
    "zeph": "Zephaniah", "zephaniah": "Zephaniah",
    "hag": "Haggai", "haggai": "Haggai",
    "zech": "Zechariah", "zechariah": "Zechariah",
    "mal": "Malachi", "malachi": "Malachi",
    "matt": "Matthew", "matthew": "Matthew", "mt": "Matthew",
    "mark": "Mark", "mk": "Mark",
    "luke": "Luke", "lk": "Luke", "luk": "Luke",
    "john": "John", "jn": "John",
    "acts": "Acts",
    "rom": "Romans", "romans": "Romans",
    "1cor": "1 Corinthians", "1corinthians": "1 Corinthians",
    "2cor": "2 Corinthians", "2corinthians": "2 Corinthians",
    "gal": "Galatians", "galatians": "Galatians",
    "eph": "Ephesians", "ephesians": "Ephesians",
    "phil": "Philippians", "philippians": "Philippians",
    "col": "Colossians", "colossians": "Colossians",
    "1thess": "1 Thessalonians", "1thessalonians": "1 Thessalonians",
    "2thess": "2 Thessalonians", "2thessalonians": "2 Thessalonians",
    "1tim": "1 Timothy", "1timothy": "1 Timothy",
    "2tim": "2 Timothy", "2timothy": "2 Timothy",
    "titus": "Titus",
    "phlm": "Philemon", "philemon": "Philemon",
    "heb": "Hebrews", "hebrews": "Hebrews",
    "jas": "James", "james": "James",
    "1pet": "1 Peter", "1peter": "1 Peter",
    "2pet": "2 Peter", "2peter": "2 Peter",
    "1john": "1 John", "1jn": "1 John",
    "2john": "2 John", "2jn": "2 John",
    "3john": "3 John", "3jn": "3 John",
    "jude": "Jude",
    "rev": "Revelation", "revelation": "Revelation", "revelations": "Revelation",
}


def _load_bible():
    global _BIBLE_CACHE
    if _BIBLE_CACHE is not None:
        return _BIBLE_CACHE
    data_path = Path(__file__).parent / "data" / "kjv.json"
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            _BIBLE_CACHE = json.load(f)
    else:
        _BIBLE_CACHE = {}
    return _BIBLE_CACHE


def normalize_book(raw):
    key = raw.strip().lower().replace(".", "").replace(" ", "")
    return _BOOK_ALIASES.get(key)


def parse_reference(ref_str):
    """Parse 'John 3:16', 'Genesis 1:1-5', 'Psalm 23', 'Romans 8:28-30'."""
    import re
    ref_str = ref_str.strip()
    m = re.match(
        r'^\s*([1-3]?\s?[A-Za-z]+)\s+(\d+)(?::(\d+)(?:-(\d+))?)?\s*$',
        ref_str,
    )
    if not m:
        return None
    book_raw = (m.group(1) or "").strip()
    book = normalize_book(book_raw)
    if not book:
        return None
    chapter = int(m.group(2))
    verse_start = int(m.group(3)) if m.group(3) else None
    verse_end = int(m.group(4)) if m.group(4) else None

    if verse_start is None:
        # Whole chapter
        display = f"{book} {chapter}"
    elif verse_end is not None:
        display = f"{book} {chapter}:{verse_start}-{verse_end}"
    else:
        display = f"{book} {chapter}:{verse_start}"

    return {
        "book": book,
        "chapter": chapter,
        "verse_start": verse_start,
        "verse_end": verse_end,
        "display": display,
    }


def build_slides(ref):
    """Build presentation slides from a parsed reference."""
    bible = _load_bible()
    book_data = bible.get(ref["book"])
    if not book_data:
        return []
    chapter_str = str(ref["chapter"])
    chapter = book_data.get(chapter_str)
    if not chapter:
        return []

    # chapter is a list of {"v": int, "text": str}
    if ref["verse_start"] is None:
        verses = chapter
    elif ref["verse_end"] is not None:
        verses = [v for v in chapter if ref["verse_start"] <= v["v"] <= ref["verse_end"]]
    else:
        verses = [v for v in chapter if v["v"] == ref["verse_start"]]

    slides = []
    # Group verses: 1 verse per slide, or up to 3 short verses per slide
    chunk = []
    for v in verses:
        chunk.append(v)
        text_len = sum(len(x["text"]) for x in chunk)
        if len(chunk) >= 3 or text_len > 200 or v is verses[-1]:
            ref_label = ref["display"]
            if len(chunk) == 1:
                vref = f'{ref["book"]} {ref["chapter"]}:{chunk[0]["v"]}'
            else:
                vref = f'{ref["book"]} {ref["chapter"]}:{chunk[0]["v"]}-{chunk[-1]["v"]}'
            text = " ".join(f'{v["v"]} {v["text"]}' for v in chunk)
            slides.append({"reference": vref, "text": text, "fullRef": ref_label})
            chunk = []
    return slides
