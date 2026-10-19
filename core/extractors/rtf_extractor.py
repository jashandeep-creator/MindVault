"""
core/extractors/rtf_extractor.py

RTF text extraction via striprtf - a small, pure-Python, dependency-
free RTF-to-text converter (no external binary, no network).
"""

from pathlib import Path
from typing import Optional

try:
    from striprtf.striprtf import rtf_to_text
except ImportError:
    rtf_to_text = None


def extract(path: Path) -> Optional[str]:
    if rtf_to_text is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = rtf_to_text(raw)
        return text if text and text.strip() else None
    except Exception:
        return None
