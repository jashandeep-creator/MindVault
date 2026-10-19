"""
core/ocr.py

Local OCR support for LifeSearch, via Tesseract OCR (through the
pytesseract wrapper) and Pillow for image loading.

Tesseract itself is a native program, not a Python package - pip only
installs the `pytesseract` wrapper, which shells out to a `tesseract`
executable that must be installed separately (see README.md). This
module is written so that missing/misconfigured Tesseract NEVER
crashes the app: every public function fails soft and reports its
status through return values, not exceptions.

All processing here is 100% local - no network calls, nothing is
uploaded anywhere.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import pytesseract
    from PIL import Image
    if Image is not None:
        Image.MAX_IMAGE_PIXELS = None
except ImportError:
    # pytesseract / Pillow aren't installed at all. OCR features are
    # simply unavailable - handled gracefully everywhere below.
    pytesseract = None
    Image = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Small local config file so a user-supplied Tesseract path survives
# restarts, without hard-coding any machine-specific path in source.
CONFIG_PATH = PROJECT_ROOT / "data" / "config.json"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def configure_tesseract_path(tesseract_cmd: str) -> None:
    """
    Point pytesseract at a specific tesseract executable - needed on
    Windows if it isn't on PATH (e.g.
    "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"). Persisted to
    data/config.json so it doesn't need to be set again next launch.
    """
    if pytesseract is not None:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    config = _load_config()
    config["tesseract_cmd"] = tesseract_cmd
    _save_config(config)


def _apply_saved_tesseract_path() -> None:
    """Re-apply a previously configured path, or auto-discover from standard
    locations on Windows, macOS, or Linux."""
    if pytesseract is None:
        return

    saved_cmd = _load_config().get("tesseract_cmd")
    if saved_cmd and os.path.exists(saved_cmd):
        pytesseract.pytesseract.tesseract_cmd = saved_cmd
        return

    # Check if system PATH already has a working tesseract
    try:
        pytesseract.get_tesseract_version()
        return
    except Exception:
        pass

    # Standard platform search locations
    import sys
    candidates = []
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.join(local_app, "Programs", "Tesseract-OCR", "tesseract.exe"),
        ]
    elif sys.platform == "darwin":  # macOS
        candidates = [
            "/opt/homebrew/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/usr/bin/tesseract",
        ]
    else:  # Linux / Unix
        candidates = [
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/usr/bin/tesseract-ocr",
        ]

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            break


_apply_saved_tesseract_path()


def is_ocr_available() -> bool:
    """
    True if pytesseract + Pillow are importable AND a working
    tesseract executable can actually be found and run. Safe to call
    anytime - never raises. This does spawn a `tesseract --version`
    subprocess, so callers should check it once per indexing run
    rather than once per file.
    """
    if pytesseract is None or Image is None:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def get_unavailable_reason() -> str:
    """
    Human-readable explanation of why OCR isn't available right now,
    suitable for display directly in the UI. Empty string if OCR is
    available.
    """
    if pytesseract is None or Image is None:
        return (
            "OCR dependencies not installed. Run: "
            "pip install -r requirements.txt"
        )
    try:
        pytesseract.get_tesseract_version()
        return ""
    except Exception:
        return (
            "Tesseract OCR engine not found. Install it separately "
            "(see README.md) - if it's not on your PATH, set its "
            "location via Settings \u2192 Configure Tesseract Path."
        )


def extract_text_from_image(path: Path) -> Optional[str]:
    """
    Run OCR on an image file and return the extracted text (stripped),
    or None if OCR isn't available, the image can't be opened, or
    extraction fails for any reason. Never raises - callers can treat
    None as "no OCR text for this image" and move on.
    """
    if pytesseract is None or Image is None:
        return None
    try:
        with Image.open(path) as img:
            return extract_text_from_pil_image(img)
    except Exception:
        # Covers TesseractNotFoundError, corrupt/unsupported images,
        # locked files, etc. - all treated the same: skip this image.
        return None


def extract_text_from_pil_image(image) -> Optional[str]:
    """
    Run OCR on an already-open Pillow Image object (as opposed to a
    file path) and return the extracted text, or None on failure/
    unavailability. Used by extractors that render their own preview
    in memory rather than reading a plain image file - currently
    core/extractors/psd_extractor.py, which composites a PSD's layers
    into a preview locally via psd-tools and OCRs that preview without
    ever writing a temp file or leaving the machine.
    """
    if pytesseract is None or Image is None:
        return None
    try:
        text = pytesseract.image_to_string(image)
        text = text.strip()
        return text if text else None
    except Exception:
        return None
