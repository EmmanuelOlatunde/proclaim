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
      goto_queue / clear / stop_countdown).

  {"type": "slide_change", "contentType": ..., "itemId": ..., "slideIndex": n,
   "slideCount": n}
      Minimal navigation message for next / prev / goto_slide. Carries no
      content; clients already hold the content from the full snapshot.

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
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from .models import Room, QueueItem, Song, SongSection, Announcement, ImageItem
from .scripture import DEFAULT_TRANSLATION_ID

STYLE_FONTS = ("default", "serif", "sans")
STYLE_SIZES = ("sm", "md", "lg")
DEFAULT_STYLES = {
    "font": "default",
    "size": "md",
    "background": {"type": "color", "value": "#05070A"},
}


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
            crossed = await self._advance(state, +1)
            await self.save_room_state(state)
            if crossed:
                await self.broadcast_full(state)
            else:
                await self.broadcast_slide(state)
        elif action == "prev":
            crossed = await self._advance(state, -1)
            await self.save_room_state(state)
            if crossed:
                await self.broadcast_full(state)
            else:
                await self.broadcast_slide(state)
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
            for chunk in _split_lyrics(s.lyrics):
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
        points at verse-1, so Prev/Next walk the chapter (and cross at its
        edges via _advance/_cross_scripture).

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
        state.update({
            "contentType": "image",
            "itemId": img.id,
            "content": {"url": img.file.url, "title": img.title},
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
        if styles.get("size") in STYLE_SIZES:
            cur["size"] = styles["size"]
        bg = styles.get("background")
        if isinstance(bg, dict) and bg.get("type") in ("color", "image") and bg.get("value"):
            cur["background"] = {"type": bg["type"], "value": str(bg["value"])}

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

    async def _advance(self, state, delta):
        """Move forward/back one slide. Returns True if the boundary was
        crossed (full-state change). Otherwise returns False (only the slide
        index moved).

        Scripture content loaded as a whole chapter: at the chapter edges the
        plain next/prev controls cross into the adjacent chapter server-side
        (Genesis 1:1 / Revelation 22:22 clamp). Otherwise, when inside a
        service queue, the boundary crosses into the next queue item.
        """
        count = max(1, state.get("slideCount", 1))
        idx = int(state.get("slideIndex", 0)) + delta
        if 0 <= idx < count:
            state["slideIndex"] = idx
            return False
        if state.get("contentType") == "scripture":
            if await self._cross_scripture(state, delta):
                return True
        qi = state.get("queueIndex", -1)
        if qi >= 0:
            if await self._goto_queue(state, qi + delta):
                return True
        state["slideIndex"] = max(0, min(idx, count - 1))
        return False

    async def _cross_scripture(self, state, delta):
        """Cross into the adjacent chapter when the operator navigates past
        the end (delta +1) or start (delta -1) of a presented chapter."""
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


def _split_lyrics(text, max_lines=6):
    """Split lyrics into slide-sized chunks by blank line or max lines."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text]
    slides = []
    for para in paragraphs:
        lines = para.split("\n")
        if len(lines) <= max_lines:
            slides.append(para)
        else:
            for i in range(0, len(lines), max_lines):
                slides.append("\n".join(lines[i:i + max_lines]))
    return slides