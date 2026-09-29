"""
HTTP views — page rendering + JSON REST API.
"""
import json
import re
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import JsonResponse, HttpResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from .models import Room, Song, SongSection, Announcement, ImageItem, QueueItem
from .song_import import parse_import_songs
from .image_pipeline import process_image, ImageProcessError
from .scripture import (
    parse_reference,
    build_slides,
    normalize_book,
    chapter_slides,
    chapter_verse_count,
    build_index,
    _load_bible,
    list_translations,
    resolve_translation,
)

MAX_IMAGE_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB, enforced before processing


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return {}


def _host(request):
    """Return the host (LAN IP:port) so the frontend can build WS URLs."""
    return request.get_host()


# --- Pages ---

def index_page(request):
    rooms = Room.objects.all()
    return render(request, "presentation/index.html", {"rooms": rooms, "host": _host(request)})


def control_page(request):
    room_code = request.GET.get("room", "CHURCH1")
    return render(request, "presentation/control.html", {"room_code": room_code, "host": _host(request)})


def display_page(request, room_code):
    Room.objects.get_or_create(code=room_code)
    return render(request, "presentation/display.html", {"room_code": room_code, "host": _host(request)})


# --- Room / Queue ---

def room_list(request):
    rooms = Room.objects.all()
    return JsonResponse({"rooms": [{"code": r.code, "name": r.name} for r in rooms]})


@csrf_exempt
def queue_manage(request, code):
    room, _ = Room.objects.get_or_create(code=code)
    if request.method == "GET":
        items = room.queue.all().order_by("order")
        return JsonResponse({"items": [q.to_dict() for q in items]})

    if request.method == "POST":
        body = _json_body(request)
        action = body.get("action")
        if action == "add":
            qi = QueueItem(
                room=room,
                order=room.queue.count(),
                item_type=body.get("item_type", "song"),
                ref_id=body.get("ref_id"),
            )
            qi.set_data(body.get("data", {}))
            qi.save()
        elif action == "remove":
            QueueItem.objects.filter(id=body.get("id")).delete()
        elif action == "reorder":
            ids = body.get("ids", [])
            for i, qid in enumerate(ids):
                QueueItem.objects.filter(id=qid, room=room).update(order=i)
        elif action == "clear":
            room.queue.all().delete()
        items = room.queue.all().order_by("order")
        return JsonResponse({"items": [q.to_dict() for q in items]})
    return JsonResponse({"error": "method not allowed"}, status=405)


# --- Songs ---

def song_list(request):
    q = request.GET.get("q", "").strip()
    language = request.GET.get("language", "").strip()
    songs = Song.objects.all()
    if language:
        songs = songs.filter(language=language)
    if q:
        # Hymnal shortcut: "nnbh 8" / "ybh 12" -> abbr + hymn number.
        # Abbreviations are search-only; never displayed as labels.
        m = re.match(r"^(ybh|nnbh)\s+(\d+)$", q, re.IGNORECASE)
        if m:
            songs = songs.filter(source_abbr=m.group(1).upper(), number=int(m.group(2)))
        elif q.isdigit():
            # Bare number: match that hymn number in EITHER hymnal. When both
            # hymnals have it, both come back (optionally filtered by language).
            songs = songs.filter(number=int(q))
        else:
            # Text search across the display title, the original file title,
            # and the actual verse/chorus body so remembered phrases match.
            songs = songs.filter(
                Q(title__icontains=q) |
                Q(original_title__icontains=q) |
                Q(sections__lyrics__icontains=q)
            ).distinct()
    return JsonResponse({"songs": [{
        "id": s.id, "title": s.title, "author": s.author,
        "language": s.language, "source": s.source, "number": s.number,
    } for s in songs]})


def song_detail(request, song_id):
    song = Song.objects.filter(id=song_id).prefetch_related("sections").first()
    if not song:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"song": song.to_dict()})


@csrf_exempt
def song_create_or_update(request):
    """Create or update a song with sections."""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    body = _json_body(request)
    sid = body.get("id")
    if sid:
        song = Song.objects.filter(id=sid).first()
        if not song:
            return JsonResponse({"error": "not found"}, status=404)
    else:
        song = Song()
    song.title = body.get("title", "")
    song.author = body.get("author", "")
    song.raw_text = body.get("raw_text", "")
    song.save()
    # Replace sections
    song.sections.all().delete()
    for i, sec in enumerate(body.get("sections", [])):
        SongSection.objects.create(
            song=song, label=sec.get("label", "Verse 1"),
            lyrics=sec.get("lyrics", ""), order=i,
        )
    return JsonResponse({"song": song.to_dict()})


@csrf_exempt
def song_delete(request, song_id):
    if request.method != "DELETE":
        return JsonResponse({"error": "method not allowed"}, status=405)
    Song.objects.filter(id=song_id).delete()
    return JsonResponse({"ok": True})


@csrf_exempt
def song_import(request):
    """Parse text/chordpro into song previews (no save).

    Returns {"songs": [song_dict, ...]} where each song_dict has title,
    title_found, language, warnings and sections. For backwards compatibility
    the FIRST song's sections are also echoed under {"sections": [...]}.
    """
    body = _json_body(request)
    text = body.get("text", "")
    fmt = body.get("format", "auto")
    if not str(text).strip():
        return JsonResponse({"songs": [], "sections": []})
    songs = parse_import_songs(text, fmt)
    return JsonResponse({
        "songs": songs,
        "sections": songs[0]["sections"] if songs else [],
    })


# --- Announcements ---

def announcement_list(request):
    q = request.GET.get("q", "")
    anns = Announcement.objects.all()
    if q:
        anns = anns.filter(title__icontains=q)
    return JsonResponse({"announcements": [a.to_dict() for a in anns]})


def announcement_detail(request, ann_id):
    ann = Announcement.objects.filter(id=ann_id).first()
    if not ann:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"announcement": ann.to_dict()})


@csrf_exempt
def announcement_create_or_update(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    body = _json_body(request)
    aid = body.get("id")
    if aid:
        ann = Announcement.objects.filter(id=aid).first()
        if not ann:
            return JsonResponse({"error": "not found"}, status=404)
    else:
        ann = Announcement()
    ann.title = body.get("title", "")
    ann.body = body.get("body", "")
    ann.save()
    return JsonResponse({"announcement": ann.to_dict()})


@csrf_exempt
def announcement_delete(request, ann_id):
    if request.method != "DELETE":
        return JsonResponse({"error": "method not allowed"}, status=405)
    Announcement.objects.filter(id=ann_id).delete()
    return JsonResponse({"ok": True})


# --- Images ---

def image_list(request):
    q = request.GET.get("q", "")
    imgs = ImageItem.objects.all()
    if q:
        imgs = imgs.filter(title__icontains=q)
    return JsonResponse({"images": [i.to_dict() for i in imgs]})


@csrf_exempt
def image_upload(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "no file"}, status=400)
    if f.size and f.size > MAX_IMAGE_UPLOAD_BYTES:
        return JsonResponse({"error": "File is larger than 15 MB."}, status=400)

    title = request.POST.get("title", "") or f.name or "Image"
    try:
        result = process_image(f.read(), f.name)
    except ImageProcessError as exc:
        return JsonResponse({"error": exc.message}, status=400)

    # Duplicate (same processed bytes) -> report, never store a second copy.
    dup = ImageItem.objects.filter(sha256=result["sha256"]).first()
    if dup:
        return JsonResponse({"duplicate": True, "image": dup.to_dict()})

    stem = uuid.uuid4().hex
    thumb_name = default_storage.save("images/%s.thumb.jpg" % stem, ContentFile(result["variants"]["thumb"]))
    soft_name = default_storage.save("images/%s.soft.jpg" % stem, ContentFile(result["variants"]["soft"]))
    strong_name = default_storage.save("images/%s.strong.jpg" % stem, ContentFile(result["variants"]["strong"]))
    img = ImageItem(
        title=(title[:200] if title else "Image"),
        file_thumb=thumb_name,
        file_soft=soft_name,
        file_strong=strong_name,
        sha256=result["sha256"],
    )
    # file.save uploads into the ImageField's upload_to ("images/"), so pass
    # just the leaf name to avoid a double "images/images/" prefix.
    img.file.save("%s.%s" % (stem, result["ext"]), ContentFile(result["main"]), save=True)
    return JsonResponse({"image": img.to_dict()})


@csrf_exempt
def image_detail(request, image_id):
    img = ImageItem.objects.filter(id=image_id).first()
    if not img:
        return JsonResponse({"error": "not found"}, status=404)

    if request.method == "POST":
        body = _json_body(request)
        fit = body.get("fit")
        if fit in ("blur", "contain", "cover"):
            img.fit = fit
            img.save(update_fields=["fit"])
        return JsonResponse({"image": img.to_dict()})

    if request.method == "DELETE":
        # Deleting an image that is in use (as any room's background at any
        # blur level, currently presented in any room, or referenced by a
        # queue entry) would silently change what service operators and the
        # congregation see. Block it and name every room involved; the queue
        # entry keeps the image's ref_id, so the operator must remove it
        # from the queue first. Refusal never deletes the queue item itself.
        blockers = image_blockers(img)
        if blockers:
            detail = "; ".join(
                "%s (%s)" % (code, ", ".join(sorted(set(reasons))))
                for code, reasons in sorted(blockers.items())
            )
            return JsonResponse(
                {"error": "Image is in use by: %s. Remove it from the service/queue before deleting." % detail,
                 "rooms": sorted(blockers.keys())},
                status=409,
            )
        for name in img.all_file_names():
            default_storage.delete(name)
        img.delete()
        return JsonResponse({"ok": True})

    # Legacy images have no variants yet: generate them lazily on first read.
    img.ensure_variants()
    return JsonResponse({"image": img.to_dict()})


def image_blockers(img):
    """Image IDs/codes of every reason an image cannot be deleted, keyed by
    room code. An image is in use when it is:
      - the current background of a room at any blur level (the state stores
        the main URL under styles.background.value regardless of blur),
      - the content currently presented in a room (contentType "image" with a
        matching itemId),
      - referenced by any QueueItem in any room (item_type "image", ref_id =
        the image's database id; an ImageField replacement would not cover
        these references, so they must block deletion).
    """
    blockers = {}
    for room in Room.objects.all():
        state = room.get_state()
        bg = (state.get("styles") or {}).get("background") or {}
        if bg.get("type") == "image" and bg.get("value") == img.file.url:
            blockers.setdefault(room.code, []).append("background")
        if state.get("contentType") == "image" and state.get("itemId") == img.id:
            blockers.setdefault(room.code, []).append("presented")
    for qi in QueueItem.objects.filter(item_type="image", ref_id=img.id).select_related("room"):
        blockers.setdefault(qi.room.code, []).append("queued")
    return blockers


def rooms_using_background(image_url):
    """Codes of every room whose current background is `image_url`.

    Room state stores the background as styles.background = {type: "image",
    value: <image URL>}; compare by URL. No room is ever hardcoded here.
    """
    rooms = []
    for room in Room.objects.all():
        state = room.get_state()
        bg = (state.get("styles") or {}).get("background") or {}
        if bg.get("type") == "image" and bg.get("value") == image_url:
            rooms.append(room.code)
    return rooms


# --- Scripture ---

def scripture_translations(request):
    """Available translations: {id, full_name, license, book_count, verse_count}.
    ids are full-name slugs; the UI must render only full_name."""
    return JsonResponse({"translations": list_translations()})


def scripture_parse(request):
    ref_str = request.GET.get("ref", "")
    translation = resolve_translation(request.GET.get("translation"))
    ref = parse_reference(ref_str)
    if not ref:
        return JsonResponse({"error": "could not parse reference"}, status=400)
    slides = build_slides(ref, translation=translation)
    if not slides:
        return JsonResponse({"error": "passage not found in local Bible"}, status=404)
    return JsonResponse({"reference": ref["display"], "slides": slides})


def scripture_books(request):
    bible = _load_bible()
    return JsonResponse({"books": list(bible.keys())})


def scripture_index(request):
    """Full 66-book picker index for a translation (default King James
    Version). The verse counts come from that translation's data so the
    chapter/verse grids always match what will be presented. Cheap to build
    (in-memory cache) and browser-cacheable.
    """
    translation = resolve_translation(request.GET.get("translation"))
    response = JsonResponse({
        "books": build_index(translation),
        "translation": translation,
    })
    response["Cache-Control"] = "public, max-age=86400"
    return response


def scripture_chapter(request):
    """Preview source for one chapter (or a single verse of one chapter).

    query params: book=<canonical or alias>, chapter=<int>[, verse=<int>,
    translation=<id>]
    """
    translation = resolve_translation(request.GET.get("translation"))
    book = normalize_book(request.GET.get("book", ""))
    if not book:
        return JsonResponse({"error": "unknown book"}, status=400)
    try:
        chapter = int(request.GET.get("chapter", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "chapter must be a number"}, status=400)
    verse_str = request.GET.get("verse", "")
    try:
        verse = int(verse_str) if verse_str else None
    except (TypeError, ValueError):
        return JsonResponse({"error": "verse must be a number"}, status=400)
    if verse is not None and (verse < 1 or verse > chapter_verse_count(book, chapter, translation)):
        return JsonResponse({"error": "verse not found in local Bible"}, status=404)
    slides = chapter_slides(book, chapter, translation)
    if not slides:
        return JsonResponse({"error": "chapter not found in local Bible"}, status=404)
    if verse is not None:
        slides = [s for s in slides if s["verse"] == verse]
    return JsonResponse({
        "reference": f"{book} {chapter}",
        "book": book,
        "chapter": chapter,
        "slides": slides,
    })
