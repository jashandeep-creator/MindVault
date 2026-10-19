"""
core/extractors/pdf_extractor.py

PDF text extraction via PyMuPDF (imported as `pymupdf` - the modern
name; the older `fitz` import alias still works but is deprecated
upstream). Pure local library, no network calls, no external binary.
"""

from pathlib import Path
from typing import Optional

try:
    import pymupdf
except ImportError:
    pymupdf = None


def is_available() -> bool:
    return pymupdf is not None


def extract(path: Path) -> Optional[str]:
    """
    Extract all text from a PDF, page by page, joined with blank
    lines. Returns None if PyMuPDF isn't installed, the file isn't a
    valid/readable PDF, or it's password-protected.
    """
    if pymupdf is None:
        return None

    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                return None  # encrypted - no password available to try
            pages = [page.get_text() for page in doc]
        text = "\n\n".join(p.strip() for p in pages if p and p.strip())
        return text if text else None
    except Exception:
        # Corrupt file, unsupported PDF variant, etc.
        return None
