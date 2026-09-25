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
    title = models.CharField(max_length=200, blank=True, default="")
    file = models.ImageField(upload_to="images/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or self.file.name

    def to_dict(self):
        return {"id": self.id, "title": self.title, "url": self.file.url}


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
