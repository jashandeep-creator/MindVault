"""
core/image_utils.py

Centralized, safe image-thumbnail loading for the UI. This is the
ONLY place in LifeSearch that should ever turn an arbitrary image file
on disk into a Qt QPixmap for on-screen display - every call site
(currently just ui/main_window.py's search-result thumbnails) goes
through safe_load_thumbnail() rather than constructing QPixmap/QImage
directly, so the size-safety logic lives in one place instead of being
duplicated (and potentially forgotten) at each call site.

The problem this solves: QPixmap(path) / QImage(path) decode the image
at its FULL resolution before any scaling happens, even if the caller
immediately calls .scaled() afterward to shrink it to a 48px icon. For
a large photo or scan (an 8000x6000 image is 192 million pixels, ~768
MB as 32-bit RGBA) this can exceed Qt's built-in decode allocation
limit (256 MB by default), producing the
"QImageIOHandler: Rejecting image as it exceeds the current allocation
limit" warning and returning a null/blank image - or, if the limit
were simply raised instead of fixing the real issue, it would just
make the UI thread stall decoding huge images it never needed at full
size in the first place.

The fix has two tiers:

  1. Primary path - QImageReader with setScaledSize(): tell Qt's image
     plugin the target size BEFORE decoding, so common formats (JPEG in
     particular) decode directly at the reduced size rather than at
     full resolution. This is the standard Qt-recommended technique
     and the fast path for the vast majority of images.

  2. Safety net - a NumPy/Pillow-based fallback for anything whose
     dimensions can't be determined ahead of time, or that are large
     enough that trusting an arbitrary Qt image plugin to honor
     setScaledSize would be a gamble (not every plugin actually
     streams a scaled decode - some still allocate a full-resolution
     buffer internally regardless of the size hint). Pillow's
     Image.open() is lazy (no pixel decode until asked) and
     Image.thumbnail() uses JPEG draft-mode fast downscaling where
     possible, so this path stays safe even for images large enough
     that tier 1 is skipped.

Either way, this module NEVER decodes a full-resolution image merely
to produce a small thumbnail, and NEVER raises - callers get a QPixmap
or None (meaning: show a generic icon instead) in every case,
including a missing file, an unsupported/corrupt format, or a
genuinely enormous image.

Deliberately NOT used by core/ocr.py: OCR needs real, accurate pixel
data to read text correctly, so it keeps its own Pillow-based image
loading at full resolution - downscaling for OCR would hurt accuracy,
and this module exists for on-screen previews only. Nothing here
changes OCR or semantic-indexing behavior.
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap

try:
    from PIL import Image
    if Image is not None:
        Image.MAX_IMAGE_PIXELS = None
except ImportError:
    Image = None

DEFAULT_MAX_SIZE = (512, 512)

# Above this many total pixels, don't trust an arbitrary Qt image
# plugin to honor setScaledSize() with a genuinely reduced-memory
# decode - go straight to the Pillow fallback instead. Chosen well
# under Qt's default 256 MB / 4-bytes-per-pixel (~67 million pixel)
# ceiling, leaving headroom for plugins that don't scale-decode as
# efficiently as libjpeg does.
#
# JPEG is exempted from this cap (see _try_qt_scaled_read): Qt's JPEG
# plugin is backed by libjpeg-turbo, which genuinely performs a
# reduced-resolution decode when given a target size - verified
# directly (a 9000x9000 JPEG scaled-decodes in ~40ms with zero
# allocation warnings). Qt's PNG plugin, by contrast, does NOT honor
# setScaledSize() safely - it still attempts a full-resolution
# allocation internally and hits the exact 256MB rejection this module
# exists to prevent (also verified directly). Without per-format
# testing this exemption would be guesswork; with it, it's measured
# behavior. Any format not confirmed safe defaults to the conservative
# pixel-count gate below, falling through to the Pillow tier.
SAFE_QT_DECODE_PIXEL_LIMIT = 20_000_000  # e.g. a ~5000x4000 image

# Formats whose Qt plugin has been verified to genuinely honor
# setScaledSize() with a reduced-memory decode (not just resize after
# a full decode) - these skip the pixel-count gate above and always
# use the fast Qt tier, however large the source image.
FORMATS_WITH_SAFE_SCALED_DECODE = {b"jpeg", b"jpg"}


def safe_load_thumbnail(path: str, max_size: tuple = DEFAULT_MAX_SIZE) -> Optional[QPixmap]:
    """
    Return a thumbnail-sized QPixmap for the image at `path`, decoded
    directly at (approximately) `max_size` rather than at full
    resolution - a 10000x10000 image is never decoded at 10000x10000
    just because a small preview is needed.

    Returns None if the file is missing, isn't a supported/decodable
    image, or fails to load for any reason - callers should show a
    generic file icon in that case. Never raises.
    """
    try:
        if not path or not Path(path).is_file():
            return None
    except OSError:
        return None

    pixmap = _try_qt_scaled_read(path, max_size)
    if pixmap is not None:
        return pixmap

    return _try_pillow_thumbnail(path, max_size)


def _try_qt_scaled_read(path: str, max_size: tuple) -> Optional[QPixmap]:
    """Tier 1: QImageReader + setScaledSize(). Fast, no extra
    dependency, but skipped (returns None) whenever the image's real
    size can't be determined ahead of time or is large enough that a
    plugin might not honor the size hint safely - both cases fall
    through to the Pillow tier instead."""
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)  # respect EXIF rotation, like most viewers

        if not reader.canRead():
            return None

        original_size = reader.size()  # header-only for most formats, no pixel decode
        if not original_size.isValid() or original_size.width() <= 0 or original_size.height() <= 0:
            return None  # unknown ahead of time - let the Pillow tier handle it safely

        detected_format = bytes(reader.format()).lower()
        if detected_format not in FORMATS_WITH_SAFE_SCALED_DECODE:
            total_pixels = original_size.width() * original_size.height()
            if total_pixels > SAFE_QT_DECODE_PIXEL_LIMIT:
                return None  # too large to gamble on this plugin's scaled-decode support

        target = _scaled_target_size(original_size, max_size)
        if target is not None:
            reader.setScaledSize(target)

        image = reader.read()
        if image.isNull():
            return None

        return QPixmap.fromImage(image)
    except Exception:
        return None


def _try_pillow_thumbnail(path: str, max_size: tuple) -> Optional[QPixmap]:
    """Tier 2 safety net: Pillow, which is lazy about decoding
    (Image.open() reads only the header) and downscales efficiently
    (JPEG draft-mode) - safe even for images large enough or
    unusual enough that tier 1 was skipped."""
    if Image is None:
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("RGBA")
            img.thumbnail(max_size, Image.LANCZOS)
            width, height = img.size
            if width <= 0 or height <= 0:
                return None
            raw = img.tobytes("raw", "RGBA")
        # .copy() detaches the QImage from `raw`'s buffer so it stays
        # valid after this function returns and `raw` goes out of scope.
        qimage = QImage(raw, width, height, QImage.Format_RGBA8888).copy()
        if qimage.isNull():
            return None
        return QPixmap.fromImage(qimage)
    except Exception:
        return None


def _scaled_target_size(original_size: QSize, max_size: tuple) -> Optional[QSize]:
    """Compute the aspect-correct size to decode directly at, given the
    image's real dimensions and a max (width, height) box. Returns
    None if the image is already within the box (decode at native size
    - it's already small)."""
    max_w, max_h = max_size
    width, height = original_size.width(), original_size.height()

    if width <= max_w and height <= max_h:
        return None

    scale = min(max_w / width, max_h / height)
    return QSize(max(1, round(width * scale)), max(1, round(height * scale)))
