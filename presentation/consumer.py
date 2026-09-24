"""
WebSocket consumer for room state synchronization.

State shape (minimal, broadcast on change):
{
  "room": "CHURCH1",
  "contentType": "song" | "scripture" | "announcement" | "image" | "countdown" | "blank" | null,
  "itemId": <id or null>,
  "content": { ... inline content needed to render ... },
  "slideIndex": 0,
  "slideCount": 1,
  "blank": false,
  "countdown": { "endTime": <ms>, "title": "..." } | null,
  "queueIndex": -1
}
"""
import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from .models import Room, QueueItem, Song, SongSection, Announcement, ImageItem


class RoomConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.room_code = self.scope["url_route"]["kwargs"]["room_code"]
        self.group = f"room_{self.room_code}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        # Send current state on connect (reconnection / late display)
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
            self._advance(state, +1)
        elif action == "prev":
            self._advance(state, -1)
        elif action == "goto_slide":
            idx = int(cmd.get("slideIndex", 0))
            state["slideIndex"] = max(0, min(idx, max(0, state.get("slideCount", 1) - 1)))
        elif action == "goto_queue":
            await self._goto_queue(state, int(cmd.get("index", 0)))
        elif action == "present_song":
            await self._present_song(state, int(cmd.get("id", 0)))
        elif action == "present_scripture":
            await self._present_scripture(state, cmd.get("reference", ""))
        elif action == "present_announcement":
            await self._present_announcement(state, int(cmd.get("id", 0)))
        elif action == "present_image":
            await self._present_image(state, int(cmd.get("id", 0)))
        elif action == "present_countdown":
            self._present_countdown(state, cmd)
        elif action == "stop_countdown":
            state["countdown"] = None
            state["contentType"] = None
            state["content"] = None
            state["slideIndex"] = 0
            state["slideCount"] = 1
        elif action == "blank":
            state["blank"] = bool(cmd.get("blank", True))
        elif action == "clear":
            state["contentType"] = None
            state["content"] = None
            state["itemId"] = None
            state["slideIndex"] = 0
            state["slideCount"] = 1
            state["countdown"] = None
            state["blank"] = False

        await self.save_room_state(state)
        await self.broadcast_state(state)

    # --- content loaders ---

    async def _present_song(self, state, song_id):
        song = await sync_to_async(Song.objects.filter(id=song_id).prefetch_related("sections").first)()
        if not song:
            return
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

    async def _present_scripture(self, state, reference):
        from .scripture import parse_reference, build_slides
        ref = parse_reference(reference)
        if not ref:
            return
        slides = build_slides(ref)
        if not slides:
            return
        state.update({
            "contentType": "scripture",
            "itemId": None,
            "content": {"reference": ref["display"], "slides": slides},
            "slideIndex": 0,
            "slideCount": len(slides),
            "countdown": None,
            "blank": False,
        })

    async def _present_announcement(self, state, ann_id):
        ann = await sync_to_async(Announcement.objects.filter(id=ann_id).first)()
        if not ann:
            return
        state.update({
            "contentType": "announcement",
            "itemId": ann.id,
            "content": {"title": ann.title, "body": ann.body},
            "slideIndex": 0,
            "slideCount": 1,
            "countdown": None,
            "blank": False,
        })

    async def _present_image(self, state, img_id):
        img = await sync_to_async(ImageItem.objects.filter(id=img_id).first)()
        if not img:
            return
        state.update({
            "contentType": "image",
            "itemId": img.id,
            "content": {"url": img.file.url, "title": img.title},
            "slideIndex": 0,
            "slideCount": 1,
            "countdown": None,
            "blank": False,
        })

    def _present_countdown(self, state, cmd):
        import time
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

    async def _goto_queue(self, state, index):
        room = await self._get_room()
        items = await sync_to_async(list)(room.queue.all().order_by("order"))
        if index < 0 or index >= len(items):
            return
        item = items[index]
        state["queueIndex"] = index
        if item.item_type == "song" and item.ref_id:
            await self._present_song(state, item.ref_id)
        elif item.item_type == "scripture":
            ref = item.get_data().get("reference", "")
            await self._present_scripture(state, ref)
        elif item.item_type == "announcement" and item.ref_id:
            await self._present_announcement(state, item.ref_id)
        elif item.item_type == "image" and item.ref_id:
            await self._present_image(state, item.ref_id)
        elif item.item_type == "countdown":
            d = item.get_data()
            self._present_countdown(state, {"seconds": d.get("seconds", 300), "title": d.get("title", "")})

    def _advance(self, state, delta):
        count = max(1, state.get("slideCount", 1))
        idx = state.get("slideIndex", 0) + delta
        if idx < 0:
            idx = 0
        if idx >= count:
            idx = count - 1
        state["slideIndex"] = idx

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
        st.setdefault("queueIndex", -1)
        return st

    async def save_room_state(self, state):
        room = await self._get_room()
        await sync_to_async(room.set_state)(state)

    async def broadcast_state(self, state):
        await self.channel_layer.group_send(
            self.group,
            {"type": "room.message", "state": state},
        )

    async def room_message(self, event):
        await self.send_json({"type": "state", "state": event["state"]})


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
