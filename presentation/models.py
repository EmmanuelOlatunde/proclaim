"""
Presentation data models — all local, persisted in SQLite.
"""
import json
from django.db import models


class Room(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # Live presentation state (JSON stored as text for SQLite simplicity)
    state = models.TextField(default="{}")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def get_state(self):
        return json.loads(self.state) if self.state else {}

    def set_state(self, st):
        self.state = json.dumps(st)
        self.save(update_fields=["state"])


class Song(models.Model):
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=200, blank=True, default="")
    ccli = models.CharField(max_length=32, blank=True, default="")
    raw_text = models.TextField(blank=True, default="")
    original_title = models.CharField(max_length=200, blank=True, default="")
    # "Yoruba" or "English" (full word, never a code) — separates the hymnals.
    language = models.CharField(max_length=20, blank=True, default="")
    # Hymnal of record, displayed to the operator (full name, never abbr).
    source = models.CharField(max_length=120, blank=True, default="")
    # Hymnal abbreviation + hymn number (search shortcut only, e.g. "nnbh 8").
    source_abbr = models.CharField(max_length=16, blank=True, default="")
    number = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["source_abbr", "number"], name="uniq_hymnal_number"),
        ]

    def __str__(self):
        return self.title

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "ccli": self.ccli,
            "original_title": self.original_title,
            "language": self.language,
            "source": self.source,
            "source_abbr": self.source_abbr,
            "number": self.number,
            "sections": [s.to_dict() for s in self.sections.all()],
        }


class SongSection(models.Model):
    song = models.ForeignKey(Song, related_name="sections", on_delete=models.CASCADE)
    label = models.CharField(max_length=40, default="Verse 1")
    lyrics = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.song.title} — {self.label}"

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "lyrics": self.lyrics,
            "order": self.order,
        }


class Announcement(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    def to_dict(self):
        return {"id": self.id, "title": self.title, "body": self.body}


class ImageItem(models.Model):
    FIT_DEFAULT = "blur"
    FIT_CHOICES = (("blur", "blur"), ("contain", "contain"), ("cover", "cover"))

    title = models.CharField(max_length=200, blank=True, default="")
    file = models.ImageField(upload_to="images/")
    # Fit mode for the wall: "blur" fills with the image's own strong
    # variant, "contain" letterboxes (black bars), "cover" crops edges.
    fit = models.CharField(max_length=10, null=True, blank=True, default=FIT_DEFAULT)
    # Processed variants (relative paths, generated with Pillow at upload).
    # thumb (320 long edge) is what every phone picker loads; soft/strong
    # are the pre-blurred background/backdrop files (no runtime blur).
    file_thumb = models.CharField(max_length=300, blank=True, default="")
    file_soft = models.CharField(max_length=300, blank=True, default="")
    file_strong = models.CharField(max_length=300, blank=True, default="")
    sha256 = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or self.file.name

    def variant_name(self, kind):
        return getattr(self, "file_" + kind, "") or ""

    def variant_url(self, kind):
        from django.conf import settings
        name = self.variant_name(kind)
        return settings.MEDIA_URL + name if name else ""

    def fit_value(self):
        return self.fit if self.fit in ("blur", "contain", "cover") else self.FIT_DEFAULT

    def all_file_names(self):
        names = [f.name for f in (self.file, ) if f]
        names += [n for n in (self.file_thumb, self.file_soft, self.file_strong) if n]
        return names

    def ensure_variants(self):
        """Idempotently (re)generate the thumb/soft/strong variants from the
        stored main file. Returns True when it generated something. Called on
        first request / backfill for images that predate the pipeline."""
        if self.file_thumb and self.file_soft and self.file_strong:
            return False
        # clear any half-written state first
        if not all((self.file_thumb, self.file_soft, self.file_strong)):
            self.file_thumb = self.file_soft = self.file_strong = ""
        from .image_pipeline import variant_files_from_source
        try:
            variants = variant_files_from_source(self.file.path)
        except Exception:
            return False
        from django.core.files.storage import default_storage
        for kind, name in (("thumb", self.file_thumb),
                           ("soft", self.file_soft),
                           ("strong", self.file_strong)):
            if name:
                default_storage.delete(name)
        prefix = self.file.name.rsplit(".", 1)[0]
        for kind, blob in variants.items():
            out_name = "%s.%s.jpg" % (prefix, kind)
            default_storage.save(out_name, blob)
            setattr(self, "file_" + kind, out_name)
        self.save(update_fields=["file_thumb", "file_soft", "file_strong"])
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "url": self.file.url if self.file else "",
            "thumb": self.variant_url("thumb") or (self.file.url if self.file else ""),
            "soft": self.variant_url("soft"),
            "strong": self.variant_url("strong"),
            "fit": self.fit_value(),
            "sha256": self.sha256,
        }


class QueueItem(models.Model):
    room = models.ForeignKey(Room, related_name="queue", on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    item_type = models.CharField(max_length=20)  # song|scripture|announcement|image|countdown
    ref_id = models.PositiveIntegerField(null=True, blank=True)  # FK to content item
    ref_data = models.TextField(default="{}")  # inline content (scripture ref, countdown config)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.room.code} #{self.order} {self.item_type}"

    def get_data(self):
        return json.loads(self.ref_data) if self.ref_data else {}

    def set_data(self, d):
        self.ref_data = json.dumps(d)

    def to_dict(self):
        return {
            "id": self.id,
            "order": self.order,
            "item_type": self.item_type,
            "ref_id": self.ref_id,
            "data": self.get_data(),
        }
