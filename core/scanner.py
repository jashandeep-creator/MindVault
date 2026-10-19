"""
core/scanner.py

Recursively walks a folder on disk and yields paths to supported
files, skipping excluded directories (VCS, virtualenvs, dependency
folders, caches) along the way.

Pure filesystem logic only - no Qt, no SQLite, no UI, no extraction
libraries. What TEXT_EXTENSIONS/DOCUMENT_EXTENSIONS actually mean for
a given file's content is core/extractors/'s job; what IMAGE_EXTENSIONS
means is core/ocr.py's job. This module only decides which files are
worth handing to one of those.
"""

import os
from pathlib import Path
from typing import Iterator

from core.extractors import DOCUMENT_EXTENSIONS, PLAIN_TEXT_EXTENSIONS

# Directories we never want to descend into.
EXCLUDED_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__"}

# Plain text/code/config files, read directly (core/extractors/text_extractor.py).
TEXT_EXTENSIONS = PLAIN_TEXT_EXTENSIONS

# Richer document formats (PDF, Word, Excel, PowerPoint, OpenDocument,
# RTF), each needing its own parsing library - see core/extractors/.
# Kept as its own set (distinct from TEXT_EXTENSIONS) so the indexer
# can route to the right pipeline without inspecting file content.

# Image types LifeSearch indexes by running local OCR over them (see
# core/ocr.py). Kept as a separate set (rather than folded into
# TEXT_EXTENSIONS) so the indexer can tell at a glance which pipeline
# a given file needs.
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
}

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS

# A handful of common dotfiles have no extension by os.path.splitext's
# rules (splitext(".gitignore") == (".gitignore", "") - it treats a
# leading dot with nothing before it as part of the name, not a
# separator). These are matched by full lowercase filename instead of
# extension; core/extractors treats their "extension" as the filename
# itself (".gitignore", ".env"), which is also how they're stored in
# the database's `extension` column.
SPECIAL_DOTFILE_NAMES = {".gitignore", ".env"}


def scan_directory(root_folder: str) -> Iterator[Path]:
    """
    Yield a Path for every supported file found recursively under
    root_folder, skipping excluded directories entirely.
    """
    for dirpath, dirnames, filenames in os.walk(root_folder):
        # Pruning dirnames in-place stops os.walk from descending into
        # excluded folders at all (much cheaper than filtering after).
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]

        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if not ext and name.lower() in SPECIAL_DOTFILE_NAMES:
                ext = name.lower()
            if ext in SUPPORTED_EXTENSIONS:
                yield Path(dirpath) / name


def get_extension(name: str) -> str:
    """
    Same extension-detection logic scan_directory uses internally,
    exposed so core/indexer.py can compute the same "extension" value
    (including the special-dotfile-as-extension case) for a path it
    already has, without re-implementing the rule.
    """
    ext = os.path.splitext(name)[1].lower()
    if not ext and name.lower() in SPECIAL_DOTFILE_NAMES:
        ext = name.lower()
    return ext
