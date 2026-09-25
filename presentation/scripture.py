"""
Scripture reference parser and slide builder.

Bundles the complete public-domain King James Version (66 books) plus
additional offline translations as local JSON files in presentation/data/.
Presentation works fully offline.

Translations are addressed by their full-name slug id (never abbreviated
anywhere user-visible). King James Version is the default.
"""
import json
import re
from pathlib import Path

_BIBLE_CACHE = {}

# Translation registry: id -> {file, full_name, license}.
# ids are full-name slugs derived from the data filenames; the full names
# (never abbreviations) are what the UI shows.
BIBLE_TRANSLATIONS = {
    "king_james_version": {
        "file": "kjv.json",
        "full_name": "King James Version",
        "license": "Public domain",
    },
    "world_english_bible": {
        "file": "world_english_bible.json",
        "full_name": "World English Bible",
        "license": "Public domain",
    },
    "american_standard_version": {
        "file": "american_standard_version.json",
        "full_name": "American Standard Version 1901",
        "license": "Public domain",
    },
    "youngs_literal_translation": {
        "file": "youngs_literal_translation.json",
        "full_name": "Young's Literal Translation",
        "license": "Public domain",
    },
    "douay_rheims_1899": {
        "file": "douay_rheims_1899.json",
        "full_name": "Douay-Rheims 1899",
        "license": "Public domain",
    },
    "darby_translation": {
        "file": "darby_translation.json",
        "full_name": "Darby Translation",
        "license": "Public domain",
    },
    "yoruba_contemporary_bible": {
        "file": "yoruba_contemporary_bible.json",
        "full_name": "Yoruba Contemporary Bible",
        "license": "CC BY-SA 4.0 © Biblica, Inc.",
    },
    "hausa_contemporary_bible": {
        "file": "hausa_contemporary_bible.json",
        "full_name": "Hausa Contemporary Bible",
        "license": "CC BY-SA 4.0 © Biblica, Inc.",
    },
    "igbo_contemporary_bible": {
        "file": "igbo_contemporary_bible.json",
        "full_name": "Igbo Contemporary Bible",
        "license": "CC BY-SA 4.0 © Biblica, Inc.",
    },
}

DEFAULT_TRANSLATION_ID = "king_james_version"
DEFAULT_TRANSLATION_NAME = "King James Version"

# Full-name labels for /api/scripture/translations, in a stable order.
TRANSLATION_ORDER = list(BIBLE_TRANSLATIONS.keys())

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


def translation_id(value):
    """Normalize a translation identifier; returns a known id or None."""
    if value is None:
        return None
    key = str(value).strip().lower().replace("-", "_")
    if key in BIBLE_TRANSLATIONS:
        return key
    return None


def resolve_translation(value):
    """Coerce a translation identifier to a known id, defaulting to KJV."""
    return translation_id(value) or DEFAULT_TRANSLATION_ID


def _load_bible(translation=DEFAULT_TRANSLATION_ID):
    """Load a translation's data, cached per translation id."""
    key = resolve_translation(translation)
    if key in _BIBLE_CACHE:
        return _BIBLE_CACHE[key]
    meta = BIBLE_TRANSLATIONS[key]
    data_path = Path(__file__).parent / "data" / meta["file"]
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            _BIBLE_CACHE[key] = json.load(f)
    else:
        _BIBLE_CACHE[key] = {}
    return _BIBLE_CACHE[key]


_LIST_CACHE = None


def _sorted_chapter_keys(book_data):
    """A book's chapter keys in ascending numeric order.

    The bundled files store chapters in arbitrary order (the converted
    translations use descending order), so any position-based helper must
    never rely on raw dict order.
    """
    return sorted(
        book_data.keys(),
        key=lambda k: int(k) if str(k).isdigit() else k,
    )


def list_translations():
    """Available translations in a stable order.

    Each entry: {id, full_name, license, book_count, verse_count}.
    Computed once and cached (the data files are immutable).
    """
    global _LIST_CACHE
    if _LIST_CACHE is not None:
        return _LIST_CACHE
    result = []
    for key in TRANSLATION_ORDER:
        bible = _load_bible(key)
        book_count = sum(1 for name in bible if bible[name])
        verse_count = sum(
            len(ch) for name in bible for ch in bible[name].values()
            if isinstance(ch, list)
        )
        result.append({
            "id": key,
            "full_name": BIBLE_TRANSLATIONS[key]["full_name"],
            "license": BIBLE_TRANSLATIONS[key]["license"],
            "book_count": book_count,
            "verse_count": verse_count,
        })
    _LIST_CACHE = result
    return _LIST_CACHE


def normalize_book(raw):
    key = raw.strip().lower().replace(".", "").replace(" ", "")
    return _BOOK_ALIASES.get(key)


# Canonical name of the first New Testament book. Used by build_index() to
# split the OT/NT toggles in the control UI.
NT_START = "Matthew"


def _canonical_books(translation=DEFAULT_TRANSLATION_ID):
    """Canonical book names in Bible order (from the bundled data)."""
    return list(_load_bible(translation).keys())


def _consume(ref_str, alias_norm):
    """Match alias characters against the start of ref_str, ignoring
    whitespace and case. Returns the index just after the matched alias,
    or None on mismatch."""
    i = 0
    for ch in alias_norm:
        while i < len(ref_str) and ref_str[i].isspace():
            i += 1
        if i >= len(ref_str) or ref_str[i].lower() != ch:
            return None
        i += 1
    return i


def _match_book(ref_str):
    """Match the longest known book name/alias at the start of ref_str.

    Handles multi-word names ("Song of Solomon"), numbered books
    ("1 John"), abbreviations ("Mt", "Ps"), and space variants
    ("Psalm" / "Psalms"). Returns (canonical_name, remainder) or (None, None).
    """
    best = None
    best_len = -1
    remainder = None
    for alias, canon in _BOOK_ALIASES.items():
        al = alias.lower()
        if len(al) <= best_len:
            continue
        i = _consume(ref_str, al)
        if i is None:
            continue
        j = i
        while j < len(ref_str) and ref_str[j].isspace():
            j += 1
        rest = ref_str[j:]
        # The chapter number must follow the book name.
        if not rest or not rest[0].isdigit():
            continue
        best = canon
        best_len = len(al)
        remainder = rest
    return best, remainder


def parse_reference(ref_str):
    """Parse 'John 3:16', 'Genesis 1:1-5', 'Psalm 23', 'Romans 8:28-30',
    '1 John 1:9', 'Song of Solomon 2:1'."""
    ref_str = (ref_str or "").strip()
    if not ref_str:
        return None

    book, remainder = _match_book(ref_str)
    if not book:
        return None

    m = re.match(r'^(\d+)(?::(\d+)(?:-(\d+))?)?$', remainder.strip())
    if not m:
        return None

    chapter = int(m.group(1))
    verse_start = int(m.group(2)) if m.group(2) else None
    verse_end = int(m.group(3)) if m.group(3) else None

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


def _select_verses(bible, ref):
    """Return the verse list for a parsed reference (whole chapter / range /
    single verse), or [] when the book or chapter is missing."""
    book_data = bible.get(ref["book"])
    if not book_data:
        return []
    chapter = book_data.get(str(ref["chapter"]))
    if not chapter:
        return []

    # chapter is a list of {"v": int, "text": str}
    if ref["verse_start"] is None:
        return chapter
    if ref["verse_end"] is not None:
        return [v for v in chapter if ref["verse_start"] <= v["v"] <= ref["verse_end"]]
    return [v for v in chapter if v["v"] == ref["verse_start"]]


def build_slides(ref, per_slide=1, translation=DEFAULT_TRANSLATION_ID):
    """Build presentation slides from a parsed reference.

    per_slide=1 (default): one slide per verse, each carrying a "verse" key.
    per_slide>1: legacy grouping (up to 3 short verses / >200 chars per
    slide) used pre-rework; retained so older callers keep their behavior.
    """
    verses = _select_verses(_load_bible(translation), ref)
    if not verses:
        return []

    if per_slide == 1:
        return [
            {
                "reference": f'{ref["book"]} {ref["chapter"]}:{v["v"]}',
                "text": v["text"],
                "fullRef": ref["display"],
                "verse": v["v"],
            }
            for v in verses
        ]

    slides = []
    # Group verses: up to 3 short verses per slide, or when text grows long.
    chunk = []
    for v in verses:
        chunk.append(v)
        text_len = sum(len(x["text"]) for x in chunk)
        if len(chunk) >= 3 or text_len > 200 or v is verses[-1]:
            if len(chunk) == 1:
                # Single verse: the slide's reference header already shows
                # the verse number (e.g. "John 3:16").
                vref = f'{ref["book"]} {ref["chapter"]}:{chunk[0]["v"]}'
                text = chunk[0]["text"]
            else:
                vref = f'{ref["book"]} {ref["chapter"]}:{chunk[0]["v"]}-{chunk[-1]["v"]}'
                text = " ".join(f'{v["v"]} {v["text"]}' for v in chunk)
            slides.append({"reference": vref, "text": text, "fullRef": ref["display"]})
            chunk = []
    return slides


def chapter_slides(book, chapter, translation=DEFAULT_TRANSLATION_ID):
    """Slides for an entire chapter, one per verse.

    Used as the presentation "load unit": presenting any verse loads the
    whole chapter and points slideIndex at verse-1, so Prev/Next can walk
    the chapter and cross into adjacent chapters at the boundaries.
    """
    bible = _load_bible(translation)
    chapter_list = bible.get(book, {}).get(str(chapter))
    if not isinstance(chapter_list, list) or not chapter_list:
        return []
    full_ref = f"{book} {chapter}"
    return [
        {
            "reference": f"{full_ref}:{v['v']}",
            "text": v["text"],
            "fullRef": full_ref,
            "verse": v["v"],
        }
        for v in chapter_list
    ]


def chapter_verse_count(book, chapter, translation=DEFAULT_TRANSLATION_ID):
    """Number of verses in a chapter (0 when the chapter does not exist)."""
    bible = _load_bible(translation)
    chapter_list = bible.get(book, {}).get(str(chapter))
    return len(chapter_list) if isinstance(chapter_list, list) else 0


def build_index(translation=DEFAULT_TRANSLATION_ID):
    """Full 66-book index for the picker UI.

    Each entry: {name, testament, aliases, chapters: [verse counts]}. The
    verse counts come from the given translation (default King James
    Version), so the chapter/verse grids always match what will be
    presented. Aliases are shared across all translations — book names and
    order are identical in every bundled file.
    """
    bible = _load_bible(translation)
    books = []
    in_nt = False
    for name in bible.keys():
        if name == NT_START:
            in_nt = True
        counts = []
        for key in _sorted_chapter_keys(bible[name]):
            chapter = bible[name][key]
            counts.append(len(chapter) if isinstance(chapter, list) else 0)
        aliases = [a for a, canon in _BOOK_ALIASES.items() if canon == name]
        books.append({
            "name": name,
            "testament": "NT" if in_nt else "OT",
            "aliases": aliases,
            "chapters": counts,
        })
    return books


def adjacent_chapter(book, chapter, delta, translation=DEFAULT_TRANSLATION_ID):
    """Return (book, chapter) one chapter away from (book, chapter).

    Crosses book boundaries: the chapter after a book's last chapter is the
    next book's first chapter and vice versa. Returns None past Genesis 1:1
    and past the last chapter of Revelation.
    """
    bible = _load_bible(translation)
    if book not in bible:
        return None
    chapter_keys = _sorted_chapter_keys(bible[book])
    try:
        idx = chapter_keys.index(str(chapter))
    except ValueError:
        return None
    nxt = idx + delta
    if 0 <= nxt < len(chapter_keys):
        return (book, int(chapter_keys[nxt]))
    book_names = list(bible.keys())
    b_idx = book_names.index(book)
    nb = b_idx + delta
    if not (0 <= nb < len(book_names)):
        return None
    if delta > 0:
        return (book_names[nb], int(_sorted_chapter_keys(bible[book_names[nb]])[0]))
    return (book_names[nb], int(_sorted_chapter_keys(bible[book_names[nb]])[-1]))


def canonicalize(value):
    """Normalize a reference in any supported form to an index tuple.

    Accepts either a reference string ("John 3:16") or a dict
    ({book, chapter, verse}); verse is optional. Returns
    {book, chapter, verse (0 = whole chapter), display} or None.
    """
    if isinstance(value, dict) and value.get("book"):
        book = normalize_book(str(value["book"]))
        if not book:
            return None
        try:
            chapter = int(value["chapter"])
        except (TypeError, ValueError):
            return None
        verse = 0
        v = value.get("verse")
        if v not in (None, "", 0):
            try:
                verse = int(v)
            except (TypeError, ValueError):
                return None
        if verse < 0:
            return None
        display = f"{book} {chapter}" + (f":{verse}" if verse else "")
        return {"book": book, "chapter": chapter, "verse": verse, "display": display}
    ref = parse_reference(value if isinstance(value, str) else str(value or ""))
    if not ref:
        return None
    return {
        "book": ref["book"],
        "chapter": ref["chapter"],
        "verse": ref["verse_start"] or 0,
        "display": ref["display"],
    }
