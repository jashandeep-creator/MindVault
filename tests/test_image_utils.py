"""
tests/test_image_utils.py

Regression tests for core/image_utils.py's safe_load_thumbnail() - the
centralized helper that replaced a direct QPixmap(path) call in
ui/main_window.py after that call was found to trigger:

    qt.gui.imageio: QImageIOHandler: Rejecting image as it exceeds the
    current allocation limit of 256 megabytes

on large images, because QPixmap(path) decodes at full resolution
before any scaling happens.

These tests build their own temporary test images (no fixture files
required) covering every format LifeSearch previews, plus the specific
failure modes that used to break: a huge image, a corrupted file, and
a missing file. Run with pytest from the project root:

    pytest tests/test_image_utils.py -v

Requires a Qt platform plugin - set QT_QPA_PLATFORM=offscreen in CI/
headless environments:

    QT_QPA_PLATFORM=offscreen pytest tests/test_image_utils.py -v
"""

import sys
import warnings
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from core.image_utils import safe_load_thumbnail

# A single QApplication is required before any Qt image/pixmap work;
# pytest-qt would normally provide this, but a bare fixture keeps this
# test file dependency-free.
_app = QApplication.instance() or QApplication(sys.argv)

MAX_ICON_SIZE = (48, 48)


@pytest.fixture
def image_dir(tmp_path: Path) -> Path:
    return tmp_path


def _assert_valid_thumbnail(pixmap, max_size=MAX_ICON_SIZE):
    assert pixmap is not None, "expected a QPixmap, got None"
    assert not pixmap.isNull()
    assert pixmap.width() <= max_size[0]
    assert pixmap.height() <= max_size[1]


class TestNormalImages:
    """1. Normal image - the common case, every format LifeSearch OCRs."""

    @pytest.mark.parametrize(
        "suffix,save_kwargs",
        [
            (".png", {}),
            (".jpg", {"quality": 90}),
            (".jpeg", {"quality": 90}),
            (".bmp", {}),
            (".tiff", {}),
            (".gif", {}),
        ],
    )
    def test_normal_image_produces_correctly_sized_thumbnail(self, image_dir, suffix, save_kwargs):
        path = image_dir / f"normal{suffix}"
        Image.new("RGB", (800, 600), (100, 150, 200)).save(path, **save_kwargs)

        pixmap = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        _assert_valid_thumbnail(pixmap)

    def test_webp(self, image_dir):
        path = image_dir / "normal.webp"
        Image.new("RGB", (800, 600), (100, 150, 200)).save(path)

        pixmap = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        _assert_valid_thumbnail(pixmap)

    def test_small_image_is_not_upscaled(self, image_dir):
        """An image already smaller than max_size should be returned
        at its native size, not stretched up to fill the box."""
        path = image_dir / "tiny.png"
        Image.new("RGB", (20, 10), (0, 0, 0)).save(path)

        pixmap = safe_load_thumbnail(str(path), max_size=(512, 512))

        assert pixmap is not None
        assert pixmap.width() == 20
        assert pixmap.height() == 10


class TestLargeImages:
    """2 & 8. Large-resolution images, including ones that would
    previously exceed Qt's 256 MB decode allocation limit."""

    def test_large_resolution_image_does_not_warn_or_crash(self, image_dir, capfd):
        """A 9000x9000 image decodes to ~324 MB as 32-bit RGBA - well
        over Qt's default 256 MB limit. This must produce a usable
        small thumbnail with NO qt.gui.imageio warning."""
        path = image_dir / "huge.png"
        Image.new("RGB", (9000, 9000), (50, 50, 50)).save(path)

        pixmap = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        _assert_valid_thumbnail(pixmap)
        captured = capfd.readouterr()
        assert "allocation limit" not in captured.err
        assert "Rejecting image" not in captured.err

    def test_huge_jpeg_uses_fast_path_and_stays_correct(self, image_dir):
        """JPEG's Qt plugin genuinely honors setScaledSize() with a
        reduced-memory decode - this should stay fast (not fall
        through to the slower Pillow tier) even at extreme sizes."""
        path = image_dir / "huge.jpg"
        Image.new("RGB", (9000, 9000), (80, 80, 80)).save(path, quality=85)

        import time
        start = time.time()
        pixmap = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)
        elapsed = time.time() - start

        _assert_valid_thumbnail(pixmap)
        assert elapsed < 1.0, f"expected the fast Qt path, took {elapsed:.2f}s"

    def test_huge_png_falls_back_but_still_succeeds(self, image_dir):
        """PNG's Qt plugin does NOT honor setScaledSize() safely (verified
        directly - it still attempts a full decode and gets rejected).
        This must still succeed via the Pillow fallback tier, just slower."""
        path = image_dir / "huge.png"
        Image.new("RGB", (9000, 9000), (10, 10, 10)).save(path)

        pixmap = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        _assert_valid_thumbnail(pixmap)

    def test_exceeds_qt_allocation_limit_reproduces_then_is_fixed(self, image_dir, capfd):
        """Explicitly reproduce the original bug report with a raw
        QPixmap first (to prove the failure mode is real), then show
        safe_load_thumbnail on the same file does not exhibit it."""
        from PySide6.QtGui import QPixmap

        path = image_dir / "reproduces_bug.png"
        Image.new("RGB", (9000, 9000), (30, 30, 30)).save(path)

        capfd.readouterr()  # clear anything buffered before this point
        naive = QPixmap(str(path))
        bug_reproduced = capfd.readouterr()
        assert naive.isNull(), "expected the naive path to fail on this input"
        if bug_reproduced.err:
            assert "allocation limit" in bug_reproduced.err

        capfd.readouterr()
        fixed = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)
        after_fix = capfd.readouterr()
        _assert_valid_thumbnail(fixed)
        assert "allocation limit" not in after_fix.err


class TestProblemFormats:
    """3, 4, 5. TIFF, BMP, WebP specifically called out in the requirements."""

    def test_tiff(self, image_dir):
        path = image_dir / "test.tiff"
        Image.new("RGB", (1200, 900), (10, 200, 10)).save(path)
        _assert_valid_thumbnail(safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE))

    def test_bmp(self, image_dir):
        path = image_dir / "test.bmp"
        Image.new("RGB", (1200, 900), (200, 10, 10)).save(path)
        _assert_valid_thumbnail(safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE))

    def test_webp(self, image_dir):
        path = image_dir / "test.webp"
        Image.new("RGB", (1200, 900), (10, 10, 200)).save(path)
        _assert_valid_thumbnail(safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE))

    def test_large_tiff_does_not_warn(self, image_dir, capfd):
        path = image_dir / "large.tiff"
        Image.new("RGB", (6000, 6000), (5, 5, 5)).save(path)
        pixmap = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)
        _assert_valid_thumbnail(pixmap)
        assert "allocation limit" not in capfd.readouterr().err


class TestFailureModes:
    """6 & 7. Corrupted and missing images must degrade to a generic
    icon (None from this helper), never crash or hang."""

    def test_corrupted_image_returns_none(self, image_dir):
        path = image_dir / "corrupted.png"
        path.write_bytes(b"not a real png file, just garbage bytes")

        result = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        assert result is None

    def test_missing_file_returns_none(self, image_dir):
        path = image_dir / "does_not_exist.png"

        result = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        assert result is None

    def test_empty_file_returns_none(self, image_dir):
        path = image_dir / "empty.png"
        path.write_bytes(b"")

        result = safe_load_thumbnail(str(path), max_size=MAX_ICON_SIZE)

        assert result is None

    def test_none_path_does_not_raise(self):
        assert safe_load_thumbnail(None, max_size=MAX_ICON_SIZE) is None

    def test_empty_string_path_does_not_raise(self):
        assert safe_load_thumbnail("", max_size=MAX_ICON_SIZE) is None


class TestOcrPipelineUnaffected:
    """7. This fix is UI-preview-only - core.ocr must keep reading
    images at full resolution, since downscaling would hurt OCR
    accuracy. safe_load_thumbnail must never be called from core.ocr."""

    def test_ocr_module_does_not_import_image_utils(self):
        import core.ocr as ocr_module
        import inspect

        source = inspect.getsource(ocr_module)
        assert "image_utils" not in source
        assert "safe_load_thumbnail" not in source

    def test_ocr_still_reads_full_resolution_image(self, image_dir):
        """A sanity check that OCR's own image loading is untouched:
        it should still be able to read a large image directly via
        Pillow (its existing pipeline), independent of image_utils."""
        from core import ocr as ocr_module

        path = image_dir / "for_ocr.png"
        Image.new("RGB", (2000, 2000), "white").save(path)

        # Not asserting on OCR text content (no Tesseract guarantee in
        # every test environment) - just that the call completes
        # without raising and without going through image_utils.
        result = ocr_module.extract_text_from_image(path)
        assert result is None or isinstance(result, str)
