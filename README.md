# MindVault

**Search your files by what you remember, completely offline.**

MindVault is an open-source, privacy-first cross-platform desktop application
(Windows, macOS, Linux) that lets you find local files using natural, memory-based
descriptions instead of exact filenames or folder paths.

> **Created by:** Jashandeep Singh  
> **Status:** V1.0.0 — Smart Hybrid Search (exact FTS5 BM25 + local AI semantic vector embeddings via Reciprocal Rank Fusion), local OCR (across 8 image formats), and local extraction for PDFs, Word, Excel, PowerPoint, OpenDocument, RTF, and Photoshop (.psd) files, all 100% offline. No cloud, no telemetry, no subscription. Universal support for Windows, macOS, and Linux.

## Tech stack

- Python 3.13+
- [PySide6](https://doc.qt.io/qtforpython/) — desktop UI
- SQLite + FTS5 — local exact full-text search index
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) via `pytesseract` + `Pillow` — local text extraction from images
- [sentence-transformers](https://www.sbert.net/) (`all-MiniLM-L6-v2`) — local semantic embeddings
- [FAISS](https://github.com/facebookresearch/faiss) (with an automatic NumPy fallback) — local vector index
- [PyMuPDF](https://pymupdf.readthedocs.io/), [python-docx](https://python-docx.readthedocs.io/), [openpyxl](https://openpyxl.readthedocs.io/), [xlrd](https://xlrd.readthedocs.io/), [python-pptx](https://python-pptx.readthedocs.io/), [odfpy](https://github.com/eea/odfpy), [striprtf](https://github.com/joshy/striprtf), [psd-tools](https://psd-tools.readthedocs.io/) — local document/design-file text extraction, one library per format family
- [watchdog](https://github.com/gorakhargosh/watchdog) — installed, not yet wired in (see Roadmap)
No AI APIs, no cloud services — everything, including the embedding model,
runs locally and offline once installed.

## Project structure

```
MindVault/
├── app.py                  # Entry point — launches the Qt application
├── core/
│   ├── database.py          # SQLite + FTS5 schema/migrations, upserts, deletion cleanup, search
│   ├── scanner.py            # Recursive folder walk, excluded dirs, extension sets, dotfile handling
│   ├── indexer.py             # Orchestrates: scan -> delete cleanup -> extract/OCR -> semantic embed -> ETA/stats
│   ├── ocr.py                  # Local Tesseract OCR wrapper (availability checks, config)
│   ├── embeddings.py            # Local sentence-transformers wrapper (availability checks)
│   ├── vector_index.py           # Local FAISS/NumPy vector store, keyed by file_id
│   ├── image_utils.py             # Safe thumbnail loading - the ONLY place that builds QPixmap/QImage from files
│   └── extractors/                # One module per document-format family (see below)
│       ├── text_extractor.py        # .txt/.md/.py/... plain text & code
│       ├── pdf_extractor.py          # .pdf (PyMuPDF)
│       ├── office_extractor.py        # .docx/.doc/.odt (python-docx, odfpy)
│       ├── spreadsheet_extractor.py    # .xlsx/.xls/.ods (openpyxl, xlrd, odfpy)
│       ├── presentation_extractor.py    # .pptx/.ppt/.odp (python-pptx, odfpy)
│       ├── rtf_extractor.py              # .rtf (striprtf)
│       └── psd_extractor.py               # .psd (psd-tools) - layers, text, controlled OCR fallback
├── tests/
│   └── test_image_utils.py          # Regression tests for safe thumbnail loading (pytest)
├── ui/
│   └── main_window.py             # UI only: search bar, Exact/Semantic selector, Add Folder, Settings menu, live ETA
├── models/                          # (reserved for future data model classes)
├── utils/                             # Small shared helper functions
├── data/
│   ├── MindVault.db                   # Local SQLite database (created on first run, gitignored)
│   ├── config.json                      # Local settings, e.g. a configured Tesseract path (gitignored)
│   └── semantic_index/                   # Local vector index files (gitignored)
├── assets/                                # Icons, fonts, static resources
├── requirements.txt
└── .gitignore
```

### How indexing works

1. Click **Add Folder** and pick a directory.
2. `core/scanner.py` recursively walks it, skipping `.git`, `.venv`,
   `venv`, `node_modules`, and `__pycache__`, yielding every supported
   text, document, and image file (see "Supported file types" below).
3. **Deletion cleanup** runs first, every scan: any previously-indexed
   file under that folder no longer found on disk is removed from
   SQLite (cascading to its FTS5 entry via trigger) and its vector is
   removed from the semantic index.
4. Each current file is processed on a background `QThread`, comparing
   size + modified-time to what's already stored — **unchanged files
   are skipped entirely**: no re-read, no re-extraction, no re-OCR, no
   re-embedding. Changed/new files are routed to the right pipeline:
   - Plain text/code → read directly.
   - PDF/Word/Excel/PowerPoint/OpenDocument/RTF → parsed via the
     matching module in `core/extractors/` (see architecture below).
   - Images → OCR'd via `core/ocr.py`.
   A successful (re)extraction resets that file's embedding status so
   step 5 picks it up.
5. **Semantic indexing** runs next, over the *whole* database — any
   file whose embedding is missing or whose content changed gets
   (re-)embedded via `core/embeddings.py` and written into
   `core/vector_index.py`. This is the same single pipeline for every
   file type: extracted text (whatever its source) → FTS5 content →
   MiniLM embedding → FAISS vector. There's no separate semantic
   system per format.
6. Live indexing progress (current file, count, percent, files/sec,
   elapsed, estimated remaining) is computed from a rolling window of
   recent per-file timestamps — not a single instantaneous rate — so
   the numbers don't jump around from file to file. On completion, a
   summary shows total elapsed time, files indexed/skipped/errored,
   OCR runs, and embeddings generated.
7. Typing in the search bar (debounced ~300ms) or pressing Enter runs
   either an FTS5 `MATCH` query (**Exact Search**, the default) or a
   query embedding compared against the vector index (**Semantic
   Search**). Double-click any result to open it in your system's
   default application.

### Extraction architecture

Each format family has its own small module in `core/extractors/`
rather than one large if/elif block. `core/extractors/__init__.py`
exposes a single `extract_text(path, extension)` entry point that
`core/indexer.py` calls without needing to know which library backs
which format. Every extractor module fails soft — a corrupt file,
missing library, or unsupported variant returns `None` rather than
raising, and the file is still indexed by filename (same pattern as an
image with OCR unavailable), never crashing the run.

### Safe thumbnails

Search-result thumbnails are loaded only through `core/image_utils.safe_load_thumbnail()`,
never by constructing a `QPixmap`/`QImage` directly from a path (that decodes at full
resolution and can trigger Qt's 256 MB image allocation limit). It decodes at the target
size via `QImageReader.setScaledSize()` where Qt's plugin honors it safely (JPEG), and
falls back to Pillow for large images in other formats (PNG/BMP/TIFF/GIF/WebP) where it
doesn't. Missing, corrupt, or undecodable images get a generic icon. OCR is unaffected and
still reads images at full resolution through its own Pillow pipeline.

### Deleted file cleanup

Every scan compares what's currently on disk under the scanned folder
against what's recorded in the database for that folder. Anything
recorded but no longer present is removed from the `files` table, the
FTS5 index (via trigger), and the semantic vector index. A file's own
SQLite row id doubles as its vector id (the simplest reliable mapping),
so deleting the row and removing the same id from the vector index
keeps both in lockstep.

## Getting started

### 1. Create a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 3. Install Tesseract OCR (required for image search)

`pytesseract` is only a thin Python wrapper — it shells out to a real
**Tesseract OCR** program, a separate native install not installed by pip.

**On Windows:** download the installer from the
[UB-Mannheim Tesseract build](https://github.com/UB-Mannheim/tesseract/wiki)
(defaults to `C:\Program Files\Tesseract-OCR\tesseract.exe`). Either add
that folder to `PATH`, or configure the path inside MindVault instead
(**Settings → Configure Tesseract Path...**).

If Tesseract isn't installed, MindVault **will not crash** — images are
indexed by filename only, with a clear on-screen explanation.

### 4. Semantic search setup

No separate install step beyond `pip install -r requirements.txt` — the
first time semantic search runs, `sentence-transformers` downloads the
`all-MiniLM-L6-v2` model weights (~90 MB) from Hugging Face once and
caches them locally; every run after that is fully offline.

If the model can't load, MindVault **will not crash** — Exact Search
keeps working normally, and Semantic Search shows a clear "unavailable"
message instead of results.

### 5. Run the app

```powershell
python app.py
```

## Supported file types

**Text / code / configuration (read directly):**
`.txt` `.md` `.py` `.java` `.js` `.html` `.css` `.json` `.csv` `.xml`
`.yaml` `.yml` `.toml` `.ini` `.cfg` `.log` `.sql` `.c` `.cpp` `.h` `.hpp`
`.cs` `.php` `.ts` `.tsx` `.jsx` `.vue` `.sh` `.bat` `.ps1` `.gitignore` `.env`

**Documents (parsed locally):**
`.pdf` `.docx` `.xlsx` `.xls` `.pptx` `.odt` `.ods` `.odp` `.rtf`
— parsed and indexed without file size limits.

**Photoshop (parsed locally via psd-tools):**
`.psd` — document dimensions and color mode, every layer/group name at any
nesting depth with visible/hidden state, and editable text-layer contents.
If a PSD's live text layers don't add up to much (e.g. a poster with text
baked into pixels rather than live type layers), a locally rendered preview
is OCR'd as a fallback via the same Tesseract pipeline — but only then, not
on every PSD, to keep OCR cost controlled. No file size limits.

**Images (OCR'd locally):**
`.png` `.jpg` `.jpeg` `.webp` `.bmp` `.tiff` `.tif` `.gif`
— OCR'd without file size or pixel count limits.

**Indexed by filename only (content not extracted):** `.doc`, `.ppt` —
the legacy pre-2007 binary Word/PowerPoint formats have no reliable
pure-Python reader without a much heavier dependency. Convert to
`.docx`/`.pptx` for full content search. See Roadmap.

**PSD extraction limitations:** Smart Object contents (embedded/linked
files) aren't recursed into — only the Smart Object layer's own name is
captured. Adjustment/effect layers contribute their name only, not a
description of the adjustment. Custom Photoshop metadata (XMP, etc.)
beyond what psd-tools' layer/composite APIs expose isn't parsed.

If extraction/OCR fails or a format isn't content-searchable, the file
is still indexed by filename and the reason is logged to the console —
indexing never stops for one bad file.

## Roadmap

- [x] Project scaffold + minimal window
- [x] SQLite + FTS5 exact search
- [x] Incremental file indexer with change detection
- [x] Local OCR for images, searchable via the same FTS5 index
- [x] Local semantic search (sentence-transformers + FAISS/NumPy)
- [x] Deleted-file cleanup across SQLite, FTS5, and the vector index
- [x] PDF/Word/Excel/PowerPoint/OpenDocument/RTF extraction
- [x] Adobe Photoshop (.psd) extraction: layers, text, controlled OCR fallback
- [x] Combined "Smart Search" mode blending exact + semantic ranking (RRF)
- [x] Index Dropdown: Folder, Drive, and Single/Multi-file indexing
- [x] Category Filter Chips (Documents, Images, Spreadsheets, Presentations, Code & Text)
- [x] Sorting controls (Relevance, Newest, Oldest, Size, Name)
- [x] Right-Click Explorer Context Menu (Open, Reveal in Explorer, Copy Path/Content, Re-index)
- [ ] Legacy `.doc`/`.ppt` binary format content extraction
- [ ] Visual (image-similarity) embeddings
- [ ] `watchdog` integration for live index updates
- [ ] Rename/move detection (currently a moved file is treated as a delete + new add)
- [ ] Retry embedding for files marked `'failed'` automatically on next scan
- [ ] Packaging for Windows (PyInstaller)

## Contributing

This project is in active development — architecture may shift as more of
the search pipeline is built out. Issues and PRs welcome.

## Contact

jashandeep.gr@gmail.com
