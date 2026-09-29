"""
WebSocket consumer for room state synchronization.

Network-efficient protocol
===========================

The server keeps the authoritative room state. It never retransmits
full presentation content when only the slide position changes.

Messages sent by the server:

  {"type": "state", "state": {...}}
      Full snapshot. Sent on connect, get_state, and whenever the
      presented *content itself* changes (present_song / present_scripture /
      present_image / present_announcement / present_countdown /
      goto_queue / next_item / prev_item / next_chapter / prev_chapter /
      clear / stop_countdown).

  {"type": "slide_change", "contentType": ..., "itemId": ..., "slideIndex": n,
   "slideCount": n}
      Minimal navigation message for next / prev / goto_slide. Carries no
      content; clients already hold the content from the full snapshot.
      next / prev are strict within-item controls: at the first or last
      slide they are ignored server-side (nothing saved, nothing
      broadcast), so the operator can never walk into the next queue item
      or across a chapter boundary by accident. Crossing items is done with
      next_item / prev_item; crossing chapters with next_chapter /
      prev_chapter.

  {"type": "blank", "blank": true|false}
      Minimal blank/unblank toggle. Position is preserved server-side.

Normal operation therefore consumes only a handful of bytes per slide
change, and never re-sends a song's lyrics or an image URL.

State shape
===========
{
  "room": "CHURCH1",
  "contentType": "song" | "scripture" | "announcement" | "image" | "countdown" | null,
  "itemId": <id or null>,
  "content": { ... inline content needed to render ... },
  "slideIndex": 0,
  "slideCount": 1,
"blank": false,
   "countdown": { "endTime": <ms>, "title": "..." } | null,
   "styles": { "font": "default"|"serif"|"sans",
               "size": "sm"|"md"|"lg",
               "background": {"type": "color", "value": "#05070A"} |
                              {"type": "image", "value": "/media/..."} },
   "translation": "world_english_bible",
   "queueIndex": -1
}

"translation" is the full-name-slug id of the active Scripture translation
(default: "king_james_version"), persisted in room state like "styles".
Changing it never re-renders the current slide; it applies from the next
present_scripture. It travels only inside the full "state" snapshot — it is
never broadcast as its own message type.

Style messages
==============

  {"type": "style_change", "styles": {...}}
      Minimal broadcast when the operator edits display appearance
      (font / text size / background). Content and position are unaffected;
      clients re-render with the new styles.
"""
import time
import json
import re
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from .models import Room, QueueItem, Song, SongSection, Announcement, ImageItem
from .scripture import DEFAULT_TRANSLATION_ID

STYLE_FONTS = ("default", "serif", "sans", "noto-sans", "noto-serif", "montserrat", "merriweather")
STYLE_SIZES = ("sm", "md", "lg")
STYLE_LINE_SPACING = ("tight", "normal", "loose")
STYLE_ALIGN = ("center", "left")
STYLE_SHADOW = ("auto", "off", "strong")
STYLE_SIZE_MIN = 0.6
STYLE_SIZE_MAX = 1.6
STYLE_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_STYLES = {
    "font": "default",
    "size": "md",
    "background": {"type": "color", "value": "#05070A"},
}
# Optional overrides: when absent the CSS keeps its built-in defaults, so the
# wall looks exactly like it always has. A null/empty value sent here clears
# the override and returns to that built-in default.
OPTIONAL_STYLE_FIELDS = ("textColor", "referenceColor", "bold", "lineSpacing", "align", "shadow")


def _valid_size(value):
    """A size is either a named preset or a number in [0.6, 1.6] rounded to a 0.05 step."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value < STYLE_SIZE_MIN or value > STYLE_SIZE_MAX:
            return None
        return round(float(value) * 20) / 20
    if isinstance(value, str) and value in STYLE_SIZES:
        return value
    return None


class RoomConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_code = self.scope["url_route"]["kwargs"]["room_code"]
        self.group = f"room_{self.room_code}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        # Send current state on connect (reconnection / late display / late operator)
        state = await self.get_room_state()
        await self.send_json({"type": "state", "state": state})

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, data):
        msg_type = data.get("type")
        if msg_type == "get_state":
            state = await self.get_room_state()
            await self.send_json({"type": "state", "state": state})
        elif msg_type == "command":
            await self.handle_command(data.get("command", {}))

    async def handle_command(self, cmd):
        action = cmd.get("action")
        state = await self.get_room_state()

        if action == "next":
            if await self._step(state, +1):
                await self.save_room_state(state)
                await self.broadcast_slide(state)
        elif action == "prev":
            if await self._step(state, -1):
                await self.save_room_state(state)
                await self.broadcast_slide(state)
        elif action == "next_item":
            qi = int(state.get("queueIndex", -1))
            if qi >= 0 and await self._goto_queue(state, qi + 1):
                await self.save_room_state(state)
                await self.broadcast_full(state)
        elif action == "prev_item":
            qi = int(state.get("queueIndex", -1))
            if qi >= 0 and await self._goto_queue(state, qi - 1):
                await self.save_room_state(state)
                await self.broadcast_full(state)
        elif action == "next_chapter":
            if await self._cross_scripture(state, +1):
                await self.save_room_state(state)
                await self.broadcast_full(state)
        elif action == "prev_chapter":
            if await self._cross_scripture(state, -1):
                await self.save_room_state(state)
                await self.broadcast_full(state)
        elif action == "goto_slide":
            idx = int(cmd.get("slideIndex", 0))
            state["slideIndex"] = max(0, min(idx, max(0, state.get("slideCount", 1) - 1)))
            await self.save_room_state(state)
            await self.broadcast_slide(state)
        elif action == "blank":
            state["blank"] = bool(cmd.get("blank", True))
            await self.save_room_state(state)
            await self.broadcast_payload({"type": "blank", "blank": state["blank"]})
        elif action == "stop_countdown":
            state["countdown"] = None
            state["contentType"] = None
            state["content"] = None
            state["itemId"] = None
            state["slideIndex"] = 0
            state["slideCount"] = 1
            await self.save_room_state(state)
            await self.broadcast_full(state)
        elif action == "clear":
            state["contentType"] = None
            state["content"] = None
            state["itemId"] = None
            state["slideIndex"] = 0
            state["slideCount"] = 1
            state["countdown"] = None
            state["blank"] = False
            state["queueIndex"] = -1
            await self.save_room_state(state)
            await self.broadcast_full(state)
        elif action == "goto_queue":
            if await self._goto_queue(state, int(cmd.get("index", 0))):
                await self.save_room_state(state)
                await self.broadcast_full(state)
        elif action == "present_song":
            await self._present_song(state, int(cmd.get("id", 0)))
            await self.save_room_state(state)
            await self.broadcast_full(state)
        elif action == "present_scripture":
            ok, err = await self._present_scripture(state, cmd)
            if ok:
                await self.save_room_state(state)
                await self.broadcast_full(state)
            else:
                # Invalid input: never broadcast; tell only the sender.
                await self.send_json({"type": "error", "message": err})
        elif action == "present_announcement":
            await self._present_announcement(state, int(cmd.get("id", 0)))
            await self.save_room_state(state)
            await self.broadcast_full(state)
        elif action == "present_image":
            await self._present_image(state, int(cmd.get("id", 0)))
            await self.save_room_state(state)
            await self.broadcast_full(state)
        elif action == "present_countdown":
            self._present_countdown(state, cmd)
            await self.save_room_state(state)
            await self.broadcast_full(state)
        elif action == "set_style":
            self._set_style(state, cmd.get("styles", {}))
            await self.save_room_state(state)
            await self.broadcast_payload({"type": "style_change", "styles": state["styles"]})
        elif action == "set_translation":
            if self._set_translation(state, cmd.get("translation")):
                await self.save_room_state(state)
                # Full snapshot only: the currently shown slide is untouched.
                await self.broadcast_full(state)
            else:
                await self.send_json({"type": "error", "message": "Unknown translation."})

    # --- content loaders ---

    async def _present_song(self, state, song_id):
        song = await sync_to_async(Song.objects.filter(id=song_id).prefetch_related("sections").first)()
        if not song:
            return False
        sections = await sync_to_async(list)(song.sections.all())
        slides = []
        for s in sections:
            for chunk in split_song_slides(s.lyrics):
                slides.append({"label": s.label, "text": chunk})
        if not slides:
            slides = [{"label": "", "text": song.title}]
        state.update({
            "contentType": "song",
            "itemId": song.id,
            "content": {"title": song.title, "author": song.author, "slides": slides},
            "slideIndex": 0,
            "slideCount": len(slides),
            "countdown": None,
            "blank": False,
        })
        return True

    async def _present_scripture(self, state, cmd):
        """Present scripture. cmd is either a dict ({book, chapter[, verse]}
        or {reference: "John 3:16"}) or a plain reference string.

        The chapter is the load unit and the verse is the navigation unit:
        the whole chapter is loaded as one-slide-per-verse and slideIndex
        points at verse-1, so Prev/Next walk the chapter within it. Crossing
        into the adjacent chapter is done explicitly with the next_chapter /
        prev_chapter commands (never by plain next/prev).

        An optional "translation" key overrides the room's stored translation
        for this presentation; otherwise the room's active translation is
        used (default King James Version). Returns (ok, error_message). On
        failure nothing is broadcast and the caller forwards the message to
        the operator's socket only.
        """
        from .scripture import canonicalize, chapter_slides, translation_id
        canon_input = cmd.get("reference") if isinstance(cmd, dict) and cmd.get("reference") else cmd
        canon = canonicalize(canon_input)
        if not canon:
            return False, "Could not parse that scripture reference."
        # Explicit translation wins; otherwise the room's stored translation
        # (default King James Version).
        explicit = translation_id(cmd.get("translation") if isinstance(cmd, dict) else None)
        translation = explicit or state.get("translation") or DEFAULT_TRANSLATION_ID
        slides = chapter_slides(canon["book"], canon["chapter"], translation)
        if not slides:
            return False, f'"{canon["book"]} {canon["chapter"]}" was not found in the local Bible.'
        idx = 0
        if canon["verse"]:
            if canon["verse"] > len(slides):
                return False, f'"{canon["book"]} {canon["chapter"]}:{canon["verse"]}" was not found in the local Bible.'
            idx = canon["verse"] - 1
        state.update({
            "contentType": "scripture",
            "itemId": None,
            "content": {
                "reference": f'{canon["book"]} {canon["chapter"]}',
                "book": canon["book"],
                "chapter": canon["chapter"],
                "slides": slides,
            },
            "slideIndex": idx,
            "slideCount": len(slides),
            "countdown": None,
            "blank": False,
            "translation": translation,
        })
        return True, None

    async def _present_announcement(self, state, ann_id):
        ann = await sync_to_async(Announcement.objects.filter(id=ann_id).first)()
        if not ann:
            return False
        state.update({
            "contentType": "announcement",
            "itemId": ann.id,
            "content": {"title": ann.title, "body": ann.body},
            "slideIndex": 0,
            "slideCount": 1,
            "countdown": None,
            "blank": False,
        })
        return True

    async def _present_image(self, state, img_id):
        img = await sync_to_async(ImageItem.objects.filter(id=img_id).first)()
        if not img:
            return False
        await sync_to_async(img.ensure_variants)()
        state.update({
            "contentType": "image",
            "itemId": img.id,
            "content": {
                "url": img.file.url,
                "title": img.title,
                "fit": img.fit_value(),
                # The display never blurs at runtime; the pre-blurred strong
                # variant is the backdrop for the default "blur" fit.
                "backdrop": img.variant_url("strong"),
            },
            "slideIndex": 0,
            "slideCount": 1,
            "countdown": None,
            "blank": False,
        })
        return True

    def _present_countdown(self, state, cmd):
        seconds = int(cmd.get("seconds", 300))
        title = cmd.get("title", "")
        state.update({
            "contentType": "countdown",
            "itemId": None,
            "content": {"title": title},
            "slideIndex": 0,
            "slideCount": 1,
            "countdown": {"endTime": int(time.time() * 1000) + seconds * 1000, "title": title},
            "blank": False,
        })
        return True

    def _set_style(self, state, styles):
        """Merge a validated styles update into the room state."""
        cur = state.setdefault("styles", dict(DEFAULT_STYLES))
        if styles.get("font") in STYLE_FONTS:
            cur["font"] = styles["font"]
        size = _valid_size(styles.get("size"))
        if size is not None:
            cur["size"] = size
        bg = styles.get("background")
        if isinstance(bg, dict) and bg.get("type") in ("color", "image") and bg.get("value"):
            cur["background"] = {"type": bg["type"], "value": str(bg["value"])}
            if bg["type"] == "image":
                # Blur level selects which PRE-BLURRED variant the display
                # loads (no runtime CSS filter). Anything else means "off".
                blur = str(bg.get("blur") or "off").lower()
                cur["background"]["blur"] = blur if blur in ("off", "soft", "strong") else "off"
        for field in OPTIONAL_STYLE_FIELDS:
            if field in styles:
                self._apply_optional_style(cur, field, styles[field])

    @staticmethod
    def _apply_optional_style(cur, field, raw):
        """Validate one optional text-style field. A null/empty value clears
        the stored override so the CSS default shows again; anything invalid
        is ignored silently."""
        if field in ("textColor", "referenceColor"):
            if raw is None or raw == "":
                cur.pop(field, None)
            elif isinstance(raw, str) and STYLE_HEX_RE.match(raw):
                cur[field] = raw.lower()
            return
        if field == "bold":
            if raw is None:
                cur.pop("bold", None)
            elif isinstance(raw, bool):
                cur["bold"] = raw
            return
        if field == "lineSpacing" and raw in STYLE_LINE_SPACING:
            cur["lineSpacing"] = raw
        elif field == "lineSpacing" and raw is None:
            cur.pop("lineSpacing", None)
        if field == "align" and raw in STYLE_ALIGN:
            cur["align"] = raw
        elif field == "align" and raw is None:
            cur.pop("align", None)
        if field == "shadow" and raw in STYLE_SHADOW:
            cur["shadow"] = raw
        elif field == "shadow" and raw is None:
            cur.pop("shadow", None)

    def _set_translation(self, state, value):
        """Set the room's active scripture translation. Returns False (and
        changes nothing) for an unknown identifier."""
        from .scripture import translation_id
        tid = translation_id(value)
        if not tid:
            return False
        state["translation"] = tid
        return True

    async def _goto_queue(self, state, index):
        room = await self._get_room()
        items = await sync_to_async(list)(room.queue.all().order_by("order"))
        if index < 0 or index >= len(items):
            return False
        item = items[index]
        state["queueIndex"] = index
        if item.item_type == "song" and item.ref_id:
            return await self._present_song(state, item.ref_id)
        elif item.item_type == "scripture":
            ref = item.get_data().get("reference", "")
            ok, _err = await self._present_scripture(state, {"reference": ref})
            return ok
        elif item.item_type == "announcement" and item.ref_id:
            return await self._present_announcement(state, item.ref_id)
        elif item.item_type == "image" and item.ref_id:
            return await self._present_image(state, item.ref_id)
        elif item.item_type == "countdown":
            d = item.get_data()
            self._present_countdown(state, {"seconds": d.get("seconds", 300), "title": d.get("title", "")})
            return True
        return False

    async def _step(self, state, delta):
        """Move one slide within the current item only.

        Returns True when the position actually moved (a minimal
        slide_change should be broadcast); at the item's first or last
        slide it is a strict no-op — the state is untouched and nothing is
        saved or broadcast. Item navigation belongs to next_item / prev_item
        and chapter navigation to next_chapter / prev_chapter.
        """
        count = max(1, state.get("slideCount", 1))
        idx = int(state.get("slideIndex", 0)) + delta
        if 0 <= idx < count:
            state["slideIndex"] = idx
            return True
        return False

    async def _cross_scripture(self, state, delta):
        """Cross into the adjacent chapter. Driven only by the explicit
        chapter buttons (next_chapter / prev_chapter) — plain next/prev
        never auto-advance a chapter."""
        from .scripture import adjacent_chapter, chapter_slides, resolve_translation
        content = state.get("content") or {}
        book = content.get("book")
        chapter = content.get("chapter")
        if not book or not chapter:
            return False
        translation = resolve_translation(state.get("translation"))
        adj = adjacent_chapter(book, chapter, delta, translation)
        if not adj:
            return False
        nbook, nchapter = adj
        slides = chapter_slides(nbook, nchapter, translation)
        if not slides:
            return False
        state["content"] = {
            "reference": f"{nbook} {nchapter}",
            "book": nbook,
            "chapter": nchapter,
            "slides": slides,
        }
        state["itemId"] = None
        state["slideIndex"] = 0 if delta > 0 else len(slides) - 1
        state["slideCount"] = len(slides)
        return True

    # --- persistence helpers ---

    async def _get_room(self):
        room, _ = await sync_to_async(Room.objects.get_or_create)(code=self.room_code)
        return room

    async def get_room_state(self):
        room = await self._get_room()
        st = room.get_state()
        if "room" not in st:
            st["room"] = self.room_code
        st.setdefault("contentType", None)
        st.setdefault("content", None)
        st.setdefault("itemId", None)
        st.setdefault("slideIndex", 0)
        st.setdefault("slideCount", 1)
        st.setdefault("blank", False)
        st.setdefault("countdown", None)
        st.setdefault("styles", dict(DEFAULT_STYLES))
        st.setdefault("translation", DEFAULT_TRANSLATION_ID)
        st.setdefault("queueIndex", -1)
        return st

    async def save_room_state(self, state):
        room = await self._get_room()
        await sync_to_async(room.set_state)(state)

    # --- broadcasts ---

    async def broadcast_full(self, state):
        await self.broadcast_payload({"type": "state", "state": state})

    async def broadcast_slide(self, state):
        await self.broadcast_payload({
            "type": "slide_change",
            "contentType": state.get("contentType"),
            "itemId": state.get("itemId"),
            "slideIndex": state.get("slideIndex", 0),
            "slideCount": state.get("slideCount", 1),
        })

    async def broadcast_payload(self, payload):
        await self.channel_layer.group_send(
            self.group,
            {"type": "room.message", "payload": payload},
        )

    async def room_message(self, event):
        await self.send_json(event["payload"])


def split_song_slides(text, max_lines=6):
    """Split lyric text into slide-sized chunks.

    A section stays on ONE slide whenever it is ``max_lines`` lines or fewer.
    Longer sections are cut at line boundaries into balanced chunks (a
    10-line section becomes 5+5, never 6+4). Blank lines separate paragraphs
    and are split first. Line bytes are preserved unchanged.

    Mirrors ``splitSongSlides`` in presentation/js/song-slides.js — keep the
    two implementations identical (see presentation/data/song_slides_fixtures.json).
    """
    if text is None:
        return []
    text = str(text)
    paras = []
    cur = []
    for ln in text.split("\n"):
        if ln.strip(" \t\u00a0") == "":
            if cur:
                paras.append(cur)
                cur = []
        else:
            cur.append(ln)
    if cur:
        paras.append(cur)
    if not paras:
        return [text]
    slides = []
    for para in paras:
        n = len(para)
        if n <= max_lines:
            slides.append("\n".join(para))
            continue
        chunks = (n + max_lines - 1) // max_lines
        base, rem = divmod(n, chunks)
        idx = 0
        for c in range(chunks):
            size = base + (1 if c < rem else 0)
            slides.append("\n".join(para[idx:idx + size]))
            idx += size
    return slides


def _split_lyrics(text, max_lines=6):
    """Back-compat alias for split_song_slides()."""
    return split_song_slides(text, max_lines)