"""
Song import parser — converts plain text or ChordPro into structured sections.

Plain text format (auto-detected):
  1. line one              verse markers: "1.", "1)", "1:", "1 " or "1.Holy"
      line two             at line start. Text may sit on the marker line or
                           on the following lines. A verse numbering sequence
                           that restarts at 1 after a higher number starts a
                           NEW song (multi-song paste).
  Verse 1 / Verse 1: / V1  also accepted as verse markers.
  Chorus / Refrain /      section markers, either on their own line or
  CH: / R: / Bridge /     prefixing the first line of the section. Bracket
  Tag / Ending / Coda     form still works: [Verse 1], [Chorus], [Bridge].
  Title line              the first non-empty line, but only when the next
                          non-empty line starts a verse. A blank line may sit
                          between the title and verse 1.

  Blank-line fallback:    text with no markers at all becomes one section per
                          paragraph (1 block per verse).

ChordPro format:
  {verse: 1}
  [C]lyrics with [G]chords

  {chorus}
  chorus text

Body lyrics are returned byte-for-byte: `’`, `‘`, `'`, diacritics and
punctuation are never normalized, re-wrapped or stripped.
"""
import re

SECTION_KEYWORDS = [
    "verse", "chorus", "bridge", "pre-chorus", "prechorus",
    "intro", "outro", "tag", "instrumental", "interlude", "ending",
]

# Named section markers -> canonical slide label (case-insensitive).
_NAMED = {
    "chorus": "Chorus", "refrain": "Chorus", "ch": "Chorus", "r": "Chorus",
    "bridge": "Bridge",
    "tag": "Tag", "ending": "Ending", "coda": "Ending",
}

_WS = " \t\u00a0"


def clean_hymn_title(title):
    """Normalize a hymnal DISPLAY title only.

    The Yoruba Baptist Hymnal uses first-line-as-title, so many titles keep a
    stray sentence-initial all-caps first word and a trailing comma (e.g.
    "EFI iyin fun Olorun,"). Fix those two things and nothing else: only the
    first word may be recased, every other character stays byte-for-byte.
    """
    text = (title or "").strip()
    while text.endswith(","):
        text = text[:-1].rstrip()
    m = re.match(r"^(\S+)", text)
    if m and m.group(1).isupper():
        word = m.group(1)
        cleaned = word[0] + word[1:].lower()
        text = cleaned + text[len(word):]
    return text


def split_hymn_sections(verses, chorus):
    """Build SongSection definitions for one hymn.

    Assumption (standard hymnal convention): a hymn with a non-empty chorus
    plays V1, Chorus, V2, Chorus, V3, Chorus, ... — the chorus is repeated
    after every verse. A single-verse hymn with a chorus is still V1, Chorus.
    Hymns with no chorus are straight V1..Vn with no chorus sections.

    Verse and chorus bodies are returned verbatim (byte-for-byte); only labels
    and ordering are synthesized here.
    """
    has_chorus = bool(chorus and str(chorus).strip())
    sections = []
    for i, verse in enumerate(verses, start=1):
        sections.append({"label": f"Verse {i}", "lyrics": str(verse)})
        if has_chorus:
            sections.append({"label": "Chorus", "lyrics": str(chorus)})
    return sections


def _normalize_newlines(text):
    """Normalize line endings (CRLF/CR from Windows, WhatsApp) to LF and drop a BOM."""
    if text is None:
        return ""
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _classify_line(line):
    """Classify one line. Returns None for blank lines, else a dict.

    Blank = empty or whitespace-only (spaces, tabs, non-breaking spaces).
    Markers are detected after stripping leading whitespace. Lyric bytes are
    preserved; only the marker prefix (and any separator after it) is consumed.
    """
    if line.strip(_WS) == "":
        return None

    t = line.lstrip(_WS)

    # Bracket header: [Verse 1], [Chorus], [Bridge] ...
    m = re.match(r"^\[(.+?)\]\s*$", t)
    if m:
        label = m.group(1).strip(_WS)
        return {"kind": "section", "label": label, "number": None, "text": ""}

    # Numbered verse: "1.", "1:53", "1)", "1.Holy", or a bare "1."
    m = re.match(r"^(\d+)\s*([.:\)])(.*)$", t)
    if m:
        n = int(m.group(1))
        return {"kind": "verse", "label": f"Verse {n}", "number": n, "text": m.group(3).strip(_WS)}

    # Numbered verse with a space separator (requires following text): "1 Holy ..."
    m = re.match(r"^(\d+)\s+(\S.*)$", t)
    if m:
        n = int(m.group(1))
        return {"kind": "verse", "label": f"Verse {n}", "number": n, "text": m.group(2).strip(_WS)}

    # "Verse 1", "Verse 1: text", "Verse 1. text"
    m = re.match(r"^[Vv]erse\s+(\d+)(?:\s*[:.]\s*|\s+)(.*)$", t)
    if m:
        n = int(m.group(1))
        return {"kind": "verse", "label": f"Verse {n}", "number": n, "text": m.group(2).strip(_WS)}
    m = re.match(r"^[Vv]erse\s+(\d+)\s*$", t)
    if m:
        n = int(m.group(1))
        return {"kind": "verse", "label": f"Verse {n}", "number": n, "text": ""}

    # "V1", "V1: text"
    m = re.match(r"^[Vv]\s*(\d+)(?:\s*[:.]\s*|\s+)(.*)$", t)
    if m:
        n = int(m.group(1))
        return {"kind": "verse", "label": f"Verse {n}", "number": n, "text": m.group(2).strip(_WS)}
    m = re.match(r"^[Vv]\s*(\d+)\s*$", t)
    if m:
        n = int(m.group(1))
        return {"kind": "verse", "label": f"Verse {n}", "number": n, "text": ""}

    # Named section markers (own line, or prefixing the first line):
    # Chorus, Chorus:, [Chorus], Refrain, CH:, R:, Bridge, Tag, Ending, Coda
    m = re.match(r"^[\[\(]?\s*([a-z]+)\s*[\)\]]?\s*:\s*(.*)$", t, re.IGNORECASE)
    if m:
        word = m.group(1).lower()
        if word in _NAMED:
            return {"kind": "section", "label": _NAMED[word], "number": None,
                    "text": m.group(2).strip(_WS)}
    m = re.match(r"^[\[\(]?\s*([a-z]+)\s*[\)\]]?\s+(.*)$", t, re.IGNORECASE)
    if m:
        word = m.group(1).lower()
        if word in _NAMED:
            return {"kind": "section", "label": _NAMED[word], "number": None,
                    "text": m.group(2).strip(_WS)}
    m = re.match(r"^[\[\(]?\s*([a-z]+)\s*[\)\]]?\s*$", t, re.IGNORECASE)
    if m:
        word = m.group(1).lower()
        if word in _NAMED:
            return {"kind": "section", "label": _NAMED[word], "number": None, "text": ""}

    # Plain lyric line — raw bytes kept intact.
    return {"kind": "lyric", "text": line}


def _build_blocks(text):
    """Turn normalized text into ordered raw blocks.

    Block kinds: "verse" (numbered marker), "section" (chorus/bracket/other
    marker), "plain" (paragraph started by an unlabelled lyric line).
    Blank lines separate paragraphs: a blank run followed by a plain lyric
    line closes whatever content-bearing block is open (so the lyric starts a
    fresh block — this is what lets a multi-song paste keep its second
    title). A blank run right after a marker with no content yet keeps the
    marker block open so the following lines stay part of it.
    """
    tokens = [(_classify_line(ln), ln) for ln in text.split("\n")]
    blocks = []
    open_block = None  # {"kind", "label", "number", "text_lines": []}

    def close():
        nonlocal open_block
        if open_block is None:
            return
        block = dict(open_block)
        block["text"] = "\n".join(block.pop("text_lines"))
        blocks.append(block)
        open_block = None

    i = 0
    n = len(tokens)
    while i < n:
        cls, ln = tokens[i]
        if cls is None:
            # Blank run. If the next non-blank line is a plain lyric, this is
            # a paragraph boundary: close the open block if it has content.
            j = i
            while j < n and tokens[j][0] is None:
                j += 1
            if j < n and tokens[j][0]["kind"] == "lyric" and open_block is not None \
                    and open_block["text_lines"]:
                close()
            i = j
            continue
        if cls["kind"] == "lyric":
            if open_block is None:
                open_block = {"kind": "plain", "label": "", "number": None, "text_lines": []}
            open_block["text_lines"].append(ln)
        else:
            close()
            open_block = {"kind": cls["kind"], "label": cls.get("label", ""),
                          "number": cls.get("number"), "text_lines": []}
            if cls.get("text"):
                open_block["text_lines"].append(cls["text"])
        i += 1
    close()
    return blocks


def _split_songs(blocks):
    """Split a flat block list into one list of blocks per song.

    A verse whose number is <= the highest number already seen starts a new
    song. Any non-verse blocks after the previous song's last verse (title /
    leading chorus) belong to the new song.
    """
    songs = []
    start = 0
    next_start = 0  # one past the most recent verse block
    max_verse = None
    for i, b in enumerate(blocks):
        if b["kind"] == "verse":
            n = b["number"]
            if max_verse is not None and n <= max_verse:
                songs.append(blocks[start:next_start])
                start = next_start
                max_verse = n
            else:
                max_verse = n if max_verse is None else max(max_verse, n)
            next_start = i + 1
    songs.append(blocks[start:])
    return songs


def _order_sections(blocks):
    """Order sections into slide order, applying the chorus convention.

    A chorus is stored once (first occurrence) and repeated after every verse
    — V1, Chorus, V2, Chorus... — unless the paste itself interleaves a chorus
    between two verses, in which case paste order wins. Other named sections
    (Bridge, Tag, Ending) and plain paragraphs keep their position; unlabelled
    paragraphs count as verses for labelling and chorus interleave.
    """
    entries = []
    verse_count = 0
    chorus_label = "Chorus"
    chorus_text = ""
    has_chorus = False
    for b in blocks:
        if b["kind"] in ("verse", "plain"):
            verse_count += 1
            entries.append({"label": f"Verse {verse_count}", "text": b["text"],
                            "is_verse": True, "is_chorus": False})
        else:  # "section"
            label = b["label"] or "Verse 1"
            if re.match(r"^verse\b", label, re.IGNORECASE):
                verse_count += 1
                entries.append({"label": label, "text": b["text"],
                                "is_verse": True, "is_chorus": False})
            elif label.lower() == "chorus":
                if not has_chorus:
                    chorus_label = label
                    chorus_text = b["text"]
                    has_chorus = True
                entries.append({"label": label, "text": b["text"],
                                "is_verse": False, "is_chorus": True})
            else:
                entries.append({"label": label, "text": b["text"],
                                "is_verse": False, "is_chorus": False})

    if not has_chorus:
        return [{"label": e["label"], "text": e["text"]} for e in entries]

    idx = [i for i, e in enumerate(entries) if e["is_chorus"]][0]
    explicit = (len(entries) > 2 and
                0 < idx < len(entries) - 1 and
                entries[idx - 1]["is_verse"] and
                entries[idx + 1]["is_verse"])

    if explicit:
        first = {}
        out = []
        for e in entries:
            if e["is_chorus"]:
                if e["label"] not in first:
                    first[e["label"]] = e["text"]
                out.append({"label": e["label"], "text": first[e["label"]]})
            else:
                out.append({"label": e["label"], "text": e["text"]})
        return out

    verses = [e for e in entries if e["is_verse"]]
    out = []
    for v in verses:
        out.append({"label": v["label"], "text": v["text"]})
        out.append({"label": chorus_label, "text": chorus_text})
    if not verses:
        out = [{"label": chorus_label, "text": chorus_text}]
    return out


def _build_song(blocks):
    """Convert one song's blocks into a song dict for the import preview."""
    if not blocks:
        return {"title": "", "title_found": False, "language": "",
                "warnings": ["No title found"], "sections": []}

    # Title: the first non-empty line, only when the next non-empty line
    # starts a verse (a blank line may sit between them).
    title = ""
    title_found = False
    bi = 0
    if len(blocks) > 1 and blocks[0]["kind"] == "plain":
        nxt = blocks[1]
        nxt_is_verse = nxt["kind"] == "verse" or (
            nxt["kind"] == "section" and re.match(r"^verse\b", nxt["label"], re.IGNORECASE))
        if nxt_is_verse:
            title = blocks[0]["text"].strip(_WS)
            title_found = bool(title)
            bi = 1

    marker_numbers = [b["number"] for b in blocks[bi:] if b["kind"] == "verse"]
    sections = [{"label": e["label"], "lyrics": e["text"]} for e in _order_sections(blocks[bi:])]

    warnings = []
    if not title_found:
        warnings.append("No title found")
    for sec in sections:
        if not sec["lyrics"].strip(_WS):
            warnings.append(f"Empty section: {sec['label']}")
    unique = sorted(set(marker_numbers))
    if len(unique) > 1:
        missing = [str(n) for n in range(unique[0], unique[-1] + 1) if n not in unique]
        if missing:
            warnings.append("Numbered verses skip: " + ", ".join(missing))

    return {"title": title, "title_found": title_found, "language": "",
            "warnings": warnings, "sections": sections}


def parse_plain_text_songs(text):
    """Parse plain text into one or more song dicts.

    Each dict: {"title", "title_found", "language", "warnings", "sections"}.
    A verse sequence restarting at 1 after a higher number is a new song.
    """
    text = _normalize_newlines(text)
    if not text.strip(_WS):
        return [{"title": "", "title_found": False, "language": "",
                 "warnings": ["No title found"], "sections": []}]
    songs = [_build_song(b) for b in _split_songs(_build_blocks(text))]
    return songs if songs else [
        {"title": "", "title_found": False, "language": "",
         "warnings": ["No title found"], "sections": []}]


def parse_plain_text(text):
    """Back-compat wrapper: first song's sections only."""
    return parse_plain_text_songs(text)[0]["sections"]


def parse_chordpro(text):
    """Parse ChordPro format, strip chords, extract sections."""
    sections = []
    current_label = None
    current_lines = []

    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        # Directive: {verse: 1}, {chorus}, {c: Verse 2}, {soc}
        m = re.match(r'^\{(.+?)\}\s*$', stripped, re.IGNORECASE)
        if m:
            directive = m.group(1).lower().strip()
            # Skip start-of-chorus/end-of-chorus markers
            if directive in ("soc", "eoc", "start_of_chorus", "end_of_chorus"):
                if directive in ("soc", "start_of_chorus") and current_label is not None:
                    sections.append({"label": current_label, "lyrics": "\n".join(current_lines).strip()})
                current_label = "Chorus" if "chorus" in directive else current_label
                current_lines = []
                continue
            # Parse label from directive
            parts = directive.replace("_", " ").split(":")
            keyword = parts[0].strip()
            value = parts[1].strip() if len(parts) > 1 else ""
            if any(k in keyword for k in SECTION_KEYWORDS):
                if current_label is not None:
                    sections.append({"label": current_label, "lyrics": "\n".join(current_lines).strip()})
                label = keyword.title()
                if value:
                    label = f"{label.title()} {value}"
                elif keyword in ("verse", "chorus", "bridge"):
                    label = keyword.title()
                    if value:
                        label += f" {value}"
                current_label = label
                current_lines = []
            continue

        # Strip chords in [brackets]
        clean = re.sub(r'\[[^\]]*\]', '', line)
        if current_label is None:
            current_label = "Verse 1"
            current_lines = []
        current_lines.append(clean)

    if current_label is not None:
        sections.append({"label": current_label, "lyrics": "\n".join(current_lines).strip()})

    if not sections:
        sections = [{"label": "Verse 1", "lyrics": text.strip()}]
    return sections


def parse_import_songs(text, fmt="auto"):
    """Parse imported lyrics into a list of song dicts (see parse_plain_text_songs).

    fmt: 'plain', 'chordpro', or 'auto'. ChordPro never produces more than one
    song; plain text may produce several from a numbering-restart paste.
    """
    if fmt == "chordpro":
        return [{"title": "", "title_found": False, "language": "",
                 "warnings": [], "sections": parse_chordpro(text)}]
    if fmt == "plain":
        return parse_plain_text_songs(text)
    # auto-detect
    if "{" in text and re.search(r'\{(verse|chorus|bridge)', text, re.IGNORECASE):
        return [{"title": "", "title_found": False, "language": "",
                 "warnings": [], "sections": parse_chordpro(text)}]
    return parse_plain_text_songs(text)


def parse_import(text, fmt="auto"):
    """Parse imported lyrics. fmt: 'plain', 'chordpro', or 'auto'.

    Returns the FIRST song's sections (list of {"label", "lyrics"}), for
    backwards compatibility. Prefer parse_import_songs() for multi-song input.
    """
    return parse_import_songs(text, fmt)[0]["sections"]
