"""
core/extractors/spreadsheet_extractor.py

Spreadsheet cell-text extraction:
  - .xlsx via openpyxl (read-only mode - fast, low memory even for
    large workbooks)
  - .xls (legacy binary format) via xlrd, which - unlike newer
    versions of itself - still reads only the old binary format,
    making it the right complement to openpyxl rather than a
    duplicate of it
  - .ods via odfpy

Cell values are joined space-separated per row, rows newline-separated,
sheets separated by a blank line, so a search for two words that sit
in the same row still matches even though FTS5 has no idea about grid
structure - it just sees flattened text.
"""

from pathlib import Path
from typing import Optional

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import xlrd
except ImportError:
    xlrd = None

try:
    from odf.opendocument import load as odf_load
    from odf.table import TableCell
    from odf import teletype as odf_teletype
except ImportError:
    odf_load = None


def extract(path: Path, extension: str) -> Optional[str]:
    ext = extension.lower()
    if ext == ".xlsx":
        return _extract_xlsx(path)
    if ext == ".xls":
        return _extract_xls(path)
    if ext == ".ods":
        return _extract_ods(path)
    return None


def _row_to_text(values) -> str:
    return " ".join(str(v) for v in values if v is not None and str(v).strip())


def _extract_xlsx(path: Path) -> Optional[str]:
    if openpyxl is None:
        return None
    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sheet_texts = []
        for sheet in workbook.worksheets:
            rows = [_row_to_text(row) for row in sheet.iter_rows(values_only=True)]
            sheet_text = "\n".join(r for r in rows if r)
            if sheet_text:
                sheet_texts.append(sheet_text)
        workbook.close()
        text = "\n\n".join(sheet_texts)
        return text if text.strip() else None
    except Exception:
        return None


def _extract_xls(path: Path) -> Optional[str]:
    if xlrd is None:
        return None
    try:
        workbook = xlrd.open_workbook(str(path))
        sheet_texts = []
        for sheet in workbook.sheets():
            rows = [
                _row_to_text(sheet.row_values(r))
                for r in range(sheet.nrows)
            ]
            sheet_text = "\n".join(r for r in rows if r)
            if sheet_text:
                sheet_texts.append(sheet_text)
        text = "\n\n".join(sheet_texts)
        return text if text.strip() else None
    except Exception:
        return None


def _extract_ods(path: Path) -> Optional[str]:
    if odf_load is None:
        return None
    try:
        document = odf_load(str(path))
        cells = document.getElementsByType(TableCell)
        parts = [odf_teletype.extractText(c) for c in cells]
        text = " ".join(p for p in parts if p and p.strip())
        return text if text.strip() else None
    except Exception:
        return None
