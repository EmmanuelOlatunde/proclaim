"""
Idempotent backfill of thumb/soft/strong variants for images that predate
the Pillow upload pipeline.

Run from the terminal:

    .venv/bin/python manage.py backfill_images

Safe to re-run: images that already have all three variants are skipped.
"""

from django.core.management.base import BaseCommand

from ...models import ImageItem


class Command(BaseCommand):
    help = "Generate thumb/soft/strong variants for legacy images."

    def handle(self, *args, **options):
        made = 0
        skipped = 0
        failed = 0
        for img in ImageItem.objects.all().iterator():
            if img.file_thumb and img.file_soft and img.file_strong:
                skipped += 1
                continue
            try:
                if img.ensure_variants():
                    made += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1
                self.stderr.write("Failed: image #%s (%s)" % (img.id, img.title or img.file.name))
        self.stdout.write(
            "backfill_images: %d generated, %d skipped, %d failed" % (made, skipped, failed)
        )