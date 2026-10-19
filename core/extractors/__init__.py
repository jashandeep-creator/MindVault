"""
core/extractors/

Text extraction, organized one module per file family rather than one
giant if/elif chain inside core/indexer.py or core/scanner.py:

  text_extractor.py          - plain text/code/config files
  pdf_extractor.py           - .pdf (PyMuPDF)
  office_extractor.py        - .docx / .doc / .odt (python-docx, odfpy)
  spreadsheet_extractor.py   - .xlsx / .xls / .ods (openpyxl, xlrd, odfpy)
  presentation_extractor.py  - .pptx / .ppt / .odp (python-pptx, odfpy)
  rtf_extractor.py           - .rtf (striprtf)
  psd_extractor.py           - .psd (psd-tools) - layer/group names,
                                visibility, editable text-layer content,
                                with a controlled local-OCR fallback

extract_text() is the single entry point core/indexer.py calls - it
doesn't need to know which library backs which extension, only the
file's extension. Every extractor module fails soft (returns None
rather than raising) on a missing library, corrupt file, or anything
else that goes wrong, so one bad document never crashes indexing - see
each module for its specific caveats (e.g. legacy .doc/.ppt binary
formats aren't extracted at all, by design - see office_extractor and
presentation_extractor).

Images are handled separately by core/ocr.py, not through this
package - OCR is a fundamentally different pipeline (image -> Tesseract)
from "open this file with a library and read its text".
"""

from pathlib import Path
from typing import Optional

from . import (
    office_extractor,
    pdf_extractor,
    presentation_extractor,
    psd_extractor,
    rtf_extractor,
    spreadsheet_extractor,
    text_extractor,
)

# Plain text / code / configuration - read directly, no parsing library.
# Includes a couple of common dotfiles-with-no-extension (see
# core/scanner.py for how those are matched despite os.path.splitext
# treating ".gitignore" and ".env" as having an empty extension).
PLAIN_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".java", ".js", ".html", ".css", ".json", ".csv",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".sql",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".php", ".ts", ".tsx", ".jsx",
    ".vue", ".sh", ".bat", ".ps1", ".gitignore", ".env",
}

PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS = {".docx", ".doc", ".odt"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".ods"}
PRESENTATION_EXTENSIONS = {".pptx", ".ppt", ".odp"}
RTF_EXTENSIONS = {".rtf"}
PSD_EXTENSIONS = {".psd"}

# Everything this package can attempt extraction on. core/scanner.py's
# DOCUMENT_EXTENSIONS should equal PDF | OFFICE | SPREADSHEET |
# PRESENTATION | RTF | PSD - imported from here so the two can't drift
# apart.
DOCUMENT_EXTENSIONS = (
    PDF_EXTENSIONS | OFFICE_EXTENSIONS | SPREADSHEET_EXTENSIONS
    | PRESENTATION_EXTENSIONS | RTF_EXTENSIONS | PSD_EXTENSIONS
)


def extract_text(path: Path, extension: str) -> Optional[str]:
    """
    Extract text from a file based on its extension, using whichever
    extractor module handles that family of formats. Returns None if
    the extension isn't recognized, the required library isn't
    installed, or extraction fails for any reason - callers treat None
    exactly like "no content for this file" (same as an image OCR
    failure), never as a crash.
    """
    ext = extension.lower()
    try:
        if ext in PLAIN_TEXT_EXTENSIONS:
            return text_extractor.extract(path)
        if ext in PDF_EXTENSIONS:
            return pdf_extractor.extract(path)
        if ext in OFFICE_EXTENSIONS:
            return office_extractor.extract(path, ext)
        if ext in SPREADSHEET_EXTENSIONS:
            return spreadsheet_extractor.extract(path, ext)
        if ext in PRESENTATION_EXTENSIONS:
            return presentation_extractor.extract(path, ext)
        if ext in RTF_EXTENSIONS:
            return rtf_extractor.extract(path)
        if ext in PSD_EXTENSIONS:
            return psd_extractor.extract(path)
    except Exception as exc:
        print(f"LifeSearch: extraction failed for {path}: {exc}")
        return None
    return None
