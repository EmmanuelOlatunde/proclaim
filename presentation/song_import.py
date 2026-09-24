"""
Song import parser — converts plain text or ChordPro into structured sections.

Plain text format:
  [Verse 1]
  lyrics here

  [Chorus]
  chorus lyrics

ChordPro format:
  {verse: 1}
  [C]lyrics with [G]chords

  {chorus}
  chorus text
"""
import re

SECTION_KEYWORDS = [
    "verse", "chorus", "bridge", "pre-chorus", "prechorus",
    "intro", "outro", "tag", "instrumental", "interlude", "ending",
]


def parse_plain_text(text):
    """Parse bracketed-section plain text into sections."""
    sections = []
    current_label = None
    current_lines = []

    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        # [Verse 1] style header
        m = re.match(r'^\[(.+?)\]\s*$', stripped)
        if m:
            if current_label is not None:
                sections.append({"label": current_label, "lyrics": "\n".join(current_lines).strip()})
            current_label = m.group(1).strip()
            current_lines = []
            continue
        if current_label is None:
            # Skip preamble until first section header
            if stripped:
                current_label = "Verse 1"
                current_lines = []
        if current_label is not None:
            current_lines.append(line)

    if current_label is not None:
        sections.append({"label": current_label, "lyrics": "\n".join(current_lines).strip()})

    if not sections:
        # No section markers — treat whole text as one verse
        sections = [{"label": "Verse 1", "lyrics": text.strip()}]
    return sections


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


def parse_import(text, fmt="auto"):
    """Parse imported lyrics. fmt: 'plain', 'chordpro', or 'auto'."""
    if fmt == "chordpro":
        return parse_chordpro(text)
    if fmt == "plain":
        return parse_plain_text(text)
    # auto-detect
    if "{" in text and re.search(r'\{(verse|chorus|bridge)', text, re.IGNORECASE):
        return parse_chordpro(text)
    if re.search(r'^\s*\[.+\]\s*$', text, re.MULTILINE):
        return parse_plain_text(text)
    # Default: plain text with bracket detection
    return parse_plain_text(text)
