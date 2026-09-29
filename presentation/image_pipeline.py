"""Pillow-based image processing for uploads and backdrop variants.

Every uploaded image is opened and re-processed with Pillow: nothing in the
media folder is ever trusted from its filename or served as-is from the
client. Variants are pre-blurred files (thumb/soft/strong) so the display
never applies a runtime CSS filter.

No new dependency is introduced (Pillow is already bundled).
"""
import hashlib
import io

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = False

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ACCEPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
HEIC_MESSAGE = (
    "HEIC/HEIF (iPhone) photos are not supported by this build. "
    "Please convert to JPEG or PNG first."
)
MAX_LONG_EDGE = 1920
JPEG_QUALITY = 85


class ImageProcessError(Exception):
    """Rejected image; `.message` is safe to return to the operator."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _looks_like_heic(filename):
    return str(filename or "").lower().endswith((".heic", ".heif"))


def _looks_like_iso_bmff(marker):
    """True when the bytes carry an ISO BMFF 'ftyp' box (HEIC/HEIF/AVIF)."""
    return marker[:4] == b"ftyp"


def _resize_long_edge(im, limit):
    """Downscale so the long edge is at most `limit`; never upscale."""
    w, h = im.size
    if max(w, h) <= limit:
        return im
    scale = limit / float(max(w, h))
    return im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def _to_rgb(im):
    if im.mode in ("RGB", "L"):
        return im.convert("RGB")
    rgba = im.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    bg.alpha_composite(rgba)
    return bg.convert("RGB")


def _encode_main(im):
    """Main file: keep PNG only when the image has real transparency."""
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue(), "png"
    buf = io.BytesIO()
    _to_rgb(im).save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue(), "jpg"


def _blur_variant(im, long_limit, radius_factor):
    v = _resize_long_edge(im, long_limit).convert("RGB")
    radius = max(12, int(v.width and max(v.size) * radius_factor))
    v = v.filter(ImageFilter.GaussianBlur(radius))
    buf = io.BytesIO()
    v.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def process_image(data, filename):
    """Validate, orient, downscale and build variants for one upload.

    Returns {
        "sha256": str,
        "ext": "jpg" | "png",
        "main": bytes,
        "variants": {"thumb": bytes, "soft": bytes, "strong": bytes},
    }
    Raises ImageProcessError with a clear message for anything rejected.
    """
    if _looks_like_heic(filename):
        raise ImageProcessError(HEIC_MESSAGE)

    # ISO BMFF 'ftyp' box => HEIC/HEIF/AVIF family (Pillow has no plugin
    # for them here); reject with a clear message instead of a crash.
    if _looks_like_iso_bmff(data[4:12]):
        brand = data[8:12].decode("latin-1", errors="ignore")
        if brand in ("heic", "heix", "hevc", "hevx", "mif1", "msf1", "heim", "heis"):
            raise ImageProcessError(HEIC_MESSAGE)
        raise ImageProcessError(
            "%s files are not supported. Please use JPEG, PNG or WebP."
            % (brand.upper() if brand else "That")
        )

    try:
        im = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        err = str(exc) or repr(exc)
        if "heif" in err.lower() or "heic" in err.lower():
            raise ImageProcessError(HEIC_MESSAGE)
        hint = (
            "HEIC/HEIF (iPhone) photos are not supported. "
            if filename and str(filename).lower().endswith((".heic", ".heif"))
            else ""
        )
        raise ImageProcessError(
            hint + "That file is not a readable JPEG, PNG or WebP image."
        )

    if im.format not in ALLOWED_FORMATS:
        raise ImageProcessError(
            "%s files are not supported. Please use JPEG, PNG or WebP."
            % (im.format or "That")
        )
    if getattr(im, "n_frames", 1) > 1:
        raise ImageProcessError("Animated images are not supported (JPEG/PNG/WebP only).")

    im = ImageOps.exif_transpose(im)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA") else im

    # Keep alpha for the main file when it is real; otherwise flatten.
    main_photo = _resize_long_edge(im, MAX_LONG_EDGE)
    main_bytes, ext = _encode_main(main_photo)

    flat = _to_rgb(main_photo)
    variants = {
        "thumb": _variant_jpg(flat, 320),
        "soft": _blur_variant(flat, 1280, 0.02),
        "strong": _blur_variant(flat, 320, 0.10),
    }
    return {
        "sha256": hashlib.sha256(main_bytes).hexdigest(),
        "ext": ext,
        "main": main_bytes,
        "variants": variants,
    }


def _variant_jpg(im, long_limit):
    v = _resize_long_edge(im, long_limit)
    buf = io.BytesIO()
    v.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def variant_files_from_source(source_path):
    """Regenerate the three variants from an existing on-disk main image.

    Used for lazy backfill of images that predate variants. Returns
    {"thumb": BytesIO, "soft": BytesIO, "strong": BytesIO} or raises.
    """
    with open(source_path, "rb") as fh:
        data = fh.read()
    result = process_image(data, source_path)
    from django.core.files.base import ContentFile
    return {
        kind: ContentFile(blob)
        for kind, blob in result["variants"].items()
    }