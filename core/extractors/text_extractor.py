"""
core/extractors/text_extractor.py

Extraction for plain-text and code/config files - anything readable
directly as UTF-8 text, no parsing library needed. This is the same
logic V2/V3 had inline in core/indexer.py, moved here so all
extraction lives in one place (core/extractors/) regardless of file
type.
"""

from pathlib import Path
from typing import Optional


def extract(path: Path) -> Optional[str]:
    """
    Read a text file's contents: try strict UTF-8 first, and fall back
    to a lossy UTF-8 decode if the file isn't valid UTF-8. Returns None
    (never raises) if the file can't be read at all.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    except OSError:
        # Permission denied, file locked by another process, file
        # disappeared, etc. - skip it rather than crash.
        return None
