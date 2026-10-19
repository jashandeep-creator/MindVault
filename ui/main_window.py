"""
ui/main_window.py

Defines the modern application window for MindVault.

Features:
  - Exact Search (SQLite FTS5), Semantic Search (sentence-transformers + FAISS),
    and Smart / Hybrid Search (Reciprocal Rank Fusion).
  - Cross-platform support across Windows, macOS, and Linux.
  - Index dropdown: Index Folder, Index Entire Drive/Volume, or Index Single File(s).
  - Category filter chips: All, Documents, Images, Spreadsheets, Presentations, Code & Text.
  - Sorting controls: Relevance, Date Modified (Newest/Oldest), Size, Name.
  - Right-click Context Menu: Open, Show in File Manager/Finder, Copy Path, Copy Name, Copy Extracted Text, Re-index File.
  - Rich card-style result presentation with safe thumbnail rendering and metadata.
  - About dialog with version details, developer credits, and offline privacy architecture.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QClipboard, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import embeddings, ocr
from core.database import DEFAULT_DB_PATH, Database
from core.extractors import (
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    PLAIN_TEXT_EXTENSIONS,
    PRESENTATION_EXTENSIONS,
    PSD_EXTENSIONS,
    RTF_EXTENSIONS,
    SPREADSHEET_EXTENSIONS,
)
from core.image_utils import safe_load_thumbnail
from core.indexer import IndexerWorker
from core.scanner import IMAGE_EXTENSIONS
from core.vector_index import VectorIndex
from utils import (
    APP_AUTHOR,
    APP_EMAIL,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    APP_WEBSITE,
    format_file_size,
    format_timestamp,
    get_available_drives,
    reveal_in_file_manager,
)

# Search debounce delay (ms)
SEARCH_DEBOUNCE_MS = 300

# Thumbnail size for image matches
THUMBNAIL_SIZE = 48

# Category definitions
DOCUMENT_EXTS = PDF_EXTENSIONS | OFFICE_EXTENSIONS | RTF_EXTENSIONS
IMAGE_EXTS = IMAGE_EXTENSIONS | PSD_EXTENSIONS
SPREADSHEET_EXTS = SPREADSHEET_EXTENSIONS | {".csv"}
PRESENTATION_EXTS = PRESENTATION_EXTENSIONS
CODE_TEXT_EXTS = PLAIN_TEXT_EXTENSIONS - {".csv"}

CATEGORIES: Dict[str, Optional[Set[str]]] = {
    "All": None,
    "Documents": DOCUMENT_EXTS,
    "Images": IMAGE_EXTS,
    "Spreadsheets": SPREADSHEET_EXTS,
    "Presentations": PRESENTATION_EXTS,
    "Code & Text": CODE_TEXT_EXTS,
}


class AboutDialog(QDialog):
    """Modern modal dialog showing MindVault version, credits, and architecture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(500, 490)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #171922;
                color: #e2e5eb;
            }
            QLabel {
                color: #d8dce6;
            }
            QPushButton {
                background-color: #3b66de;
                color: #ffffff;
                font-weight: 600;
                border-radius: 8px;
                padding: 8px 24px;
                border: none;
            }
            QPushButton:hover {
                background-color: #4a75ee;
            }
            QFrame#cardFrame {
                background-color: #1f222e;
                border: 1px solid #2b3040;
                border-radius: 10px;
                padding: 12px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(16)

        # Header: Logo + App Title + Version Badge
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        base_dirs = [
            Path(getattr(sys, "_MEIPASS", "")),
            Path(sys.executable).parent / "_internal",
            Path(sys.executable).parent,
            Path(__file__).resolve().parent.parent,
        ]
        logo_path = None
        for b in base_dirs:
            if not str(b) or not b.exists():
                continue
            cand = b / "assets" / "logo.png"
            if cand.exists():
                logo_path = cand
                break

        if logo_path and logo_path.exists():
            from PySide6.QtGui import QPixmap
            logo_pix = QPixmap(str(logo_path)).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_lbl = QLabel(self)
            logo_lbl.setPixmap(logo_pix)
            header_layout.addWidget(logo_lbl)

        title_label = QLabel(APP_NAME, self)
        title_label.setStyleSheet("font-size: 24px; font-weight: 700; color: #ffffff;")

        ver_badge = QLabel(f"v{APP_VERSION}", self)
        ver_badge.setStyleSheet(
            "background-color: #2e4480; color: #8bb2ff; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 10px;"
        )

        header_layout.addWidget(title_label)
        header_layout.addWidget(ver_badge)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        # Tagline
        tagline_label = QLabel(APP_TAGLINE, self)
        tagline_label.setStyleSheet("color: #9aa1b2; font-size: 13px; font-style: italic;")
        tagline_label.setWordWrap(True)
        layout.addWidget(tagline_label)

        # Credits & Compatibility Card
        info_card = QFrame(self)
        info_card.setObjectName("cardFrame")
        card_layout = QVBoxLayout(info_card)
        card_layout.setSpacing(10)

        author_label = QLabel(f"<b>Created & Developed by:</b> {APP_AUTHOR}", info_card)
        author_label.setStyleSheet("font-size: 13px; color: #f0f3fa;")
        card_layout.addWidget(author_label)

        plat_label = QLabel("<b>Platforms:</b> Windows • macOS • Linux (Cross-Platform)", info_card)
        plat_label.setStyleSheet("font-size: 13px; color: #f0f3fa;")
        card_layout.addWidget(plat_label)

        priv_label = QLabel("<b>Privacy:</b> 100% Offline • No Cloud • Zero Data Collection", info_card)
        priv_label.setStyleSheet("font-size: 13px; color: #52c41a;")
        card_layout.addWidget(priv_label)

        contact_label = QLabel(f"<b>Contact:</b> <a style='color: #4d7cfe;' href='mailto:{APP_EMAIL}'>{APP_EMAIL}</a>", info_card)
        contact_label.setOpenExternalLinks(True)
        contact_label.setStyleSheet("font-size: 13px;")
        card_layout.addWidget(contact_label)

        layout.addWidget(info_card)

        # Architecture Overview
        arch_label = QLabel(
            "<b>Search Architecture:</b><br>"
            "• Exact Search: SQLite + FTS5 with BM25 ranking<br>"
            "• Semantic Search: all-MiniLM-L6-v2 + FAISS/NumPy<br>"
            "• Smart Hybrid: Reciprocal Rank Fusion (RRF)<br>"
            "• Local OCR: Tesseract engine across 8 image formats<br>"
            "• Universal Parsers: PDF, Office, Spreadsheets, PSD, RTF, Text",
            self,
        )
        arch_label.setStyleSheet("color: #8890a4; font-size: 12px; line-height: 1.4;")
        layout.addWidget(arch_label)

        layout.addStretch(1)

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)


class MainWindow(QMainWindow):
    """Modernized MindVault main window."""

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle(f"{APP_NAME} — {APP_TAGLINE}")
        self.resize(1000, 680)
        self.setMinimumSize(700, 480)

        # Database and VectorIndex connections for search
        self.db = Database(DEFAULT_DB_PATH)
        self.vector_index = VectorIndex()

        # Search & filter states
        self.search_mode = "hybrid"  # "hybrid", "exact", "semantic"
        self.active_category = "All"
        self.active_sort = "relevance"
        self._all_results: List[Dict[str, Any]] = []

        # Background indexing thread tracking
        self._index_thread: Optional[QThread] = None
        self._index_worker: Optional[IndexerWorker] = None

        # Thumbnail cache: full_path -> QIcon
        self._thumbnail_cache: Dict[str, QIcon] = {}

        # Debounced search timer
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._run_search)

        self._build_ui()
        self._build_menu()
        self._connect_signals()
        self._refresh_empty_state()
        self._update_ocr_status()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(14)

        # --- Top Search Bar Row ---
        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        self.search_bar = QLineEdit(central)
        self.search_bar.setPlaceholderText("Search files by keywords, content, memories, or OCR text...")
        self.search_bar.setClearButtonEnabled(True)

        # Search Mode Dropdown (Exact, Semantic, Hybrid)
        self.mode_selector = QComboBox(central)
        self.mode_selector.addItem("✨ Smart (Hybrid)", userData="hybrid")
        self.mode_selector.addItem("🔍 Exact Search", userData="exact")
        self.mode_selector.addItem("🧠 Semantic Search", userData="semantic")
        self.mode_selector.setCursor(Qt.PointingHandCursor)
        self.mode_selector.setToolTip("Select search algorithm: Smart blends exact keyword matching with AI semantic search.")

        # Index / Add Dropdown Button
        self.add_button = QPushButton("  + Index / Add ▾  ", central)
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setObjectName("primaryAddBtn")

        # Dropdown menu for Add button
        self.add_menu = QMenu(self)
        self.add_menu.setObjectName("addDropdownMenu")

        act_folder = self.add_menu.addAction("📁  Index Folder...")
        act_folder.triggered.connect(self._choose_folder)

        drive_label = "💾  Index Drive..." if sys.platform == "win32" else "💾  Index Volume / Drive..."
        act_drive = self.add_menu.addAction(drive_label)
        act_drive.triggered.connect(self._choose_drive)

        self.add_menu.addSeparator()

        act_file = self.add_menu.addAction("📄  Index Single File(s)...")
        act_file.triggered.connect(self._choose_files)

        self.add_button.setMenu(self.add_menu)

        search_row.addWidget(self.search_bar, stretch=1)
        search_row.addWidget(self.mode_selector)
        search_row.addWidget(self.add_button)
        main_layout.addLayout(search_row)

        # --- Filter Chips & Sorting Row ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self.category_btn_group = QButtonGroup(self)
        self.category_btn_group.setExclusive(True)

        self.filter_buttons: Dict[str, QPushButton] = {}
        for cat_name in CATEGORIES.keys():
            btn = QPushButton(cat_name, central)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("isChip", True)
            if cat_name == "All":
                btn.setChecked(True)
            self.category_btn_group.addButton(btn)
            self.filter_buttons[cat_name] = btn
            btn.clicked.connect(lambda checked=False, name=cat_name: self._on_category_changed(name))
            filter_row.addWidget(btn)

        filter_row.addStretch(1)

        # Result count badge / label
        self.count_badge = QLabel("", central)
        self.count_badge.setStyleSheet("color: #8e95a5; font-size: 12px; font-weight: 500;")
        filter_row.addWidget(self.count_badge)

        # Sort Dropdown
        self.sort_selector = QComboBox(central)
        self.sort_selector.addItem("Sort: Best Match", userData="relevance")
        self.sort_selector.addItem("Sort: Newest First", userData="newest")
        self.sort_selector.addItem("Sort: Oldest First", userData="oldest")
        self.sort_selector.addItem("Sort: Largest Size", userData="largest")
        self.sort_selector.addItem("Sort: Smallest Size", userData="smallest")
        self.sort_selector.addItem("Sort: Name (A-Z)", userData="name")
        self.sort_selector.setCursor(Qt.PointingHandCursor)
        self.sort_selector.currentIndexChanged.connect(self._on_sort_changed)
        filter_row.addWidget(self.sort_selector)

        main_layout.addLayout(filter_row)

        # --- Status / Progress Bar Row ---
        status_container = QWidget(central)
        status_layout = QVBoxLayout(status_container)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)

        self.status_label = QLabel("", central)
        self.status_label.setStyleSheet("color: #9aa1b2; font-size: 12px;")
        self.status_label.setWordWrap(True)

        self.progress_bar = QProgressBar(central)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()

        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        main_layout.addWidget(status_container)

        # --- Results Area ---
        self.results_label = QLabel("No results yet — start typing above.", central)
        self.results_label.setAlignment(Qt.AlignCenter)
        self.results_label.setStyleSheet("color: #7b8294; font-size: 14px; padding: 40px;")

        self.results_list = QListWidget(central)
        self.results_list.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.results_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_list.setSpacing(4)
        self.results_list.hide()

        main_layout.addWidget(self.results_label)
        main_layout.addWidget(self.results_list, stretch=1)

        self._apply_styling()

    def _apply_styling(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #121316;
            }
            QMenuBar {
                background-color: #161820;
                color: #f0f2f7;
                font-size: 13px;
                font-weight: 500;
                border-bottom: 1px solid #282c37;
                padding: 3px 6px;
            }
            QMenuBar::item {
                background: transparent;
                color: #f0f2f7;
                padding: 5px 12px;
                border-radius: 6px;
                margin-right: 4px;
            }
            QMenuBar::item:selected {
                background-color: #28334f;
                color: #ffffff;
            }
            QMenuBar::item:pressed {
                background-color: #3b66de;
                color: #ffffff;
            }
            QWidget {
                color: #e2e5eb;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Ubuntu", "Cantarell", "Helvetica Neue", Arial, sans-serif;
            }
            QLineEdit {
                font-size: 14px;
                padding: 10px 14px;
                border: 1px solid #282c37;
                border-radius: 8px;
                background-color: #1a1c23;
                color: #f3f5f8;
            }
            QLineEdit:focus {
                border: 1px solid #4d7cfe;
                background-color: #1e212b;
            }
            QComboBox {
                font-size: 13px;
                padding: 8px 12px;
                border-radius: 8px;
                background-color: #1a1c23;
                color: #d8dce6;
                border: 1px solid #282c37;
            }
            QComboBox:hover {
                border-color: #383e4e;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1c23;
                color: #e2e5eb;
                selection-background-color: #3b66de;
                border: 1px solid #282c37;
                padding: 4px;
                border-radius: 6px;
            }
            QPushButton#primaryAddBtn {
                font-size: 13px;
                font-weight: 600;
                padding: 9px 18px;
                border-radius: 8px;
                background-color: #3b66de;
                color: #ffffff;
                border: none;
            }
            QPushButton#primaryAddBtn:hover {
                background-color: #4a75ee;
            }
            QPushButton#primaryAddBtn:disabled {
                background-color: #2b2e38;
                color: #666c7a;
            }
            QPushButton[isChip="true"] {
                font-size: 12px;
                font-weight: 500;
                padding: 6px 14px;
                border-radius: 14px;
                background-color: #1a1c23;
                color: #9aa1b2;
                border: 1px solid #282c37;
            }
            QPushButton[isChip="true"]:hover {
                background-color: #222631;
                color: #d8dce6;
                border-color: #383e4e;
            }
            QPushButton[isChip="true"]:checked {
                background-color: #2e4480;
                color: #ffffff;
                border: 1px solid #4d7cfe;
            }
            QMenu {
                background-color: #1a1c23;
                border: 1px solid #2d3240;
                border-radius: 8px;
                padding: 6px 0px;
                color: #e2e5eb;
            }
            QMenu::item {
                padding: 8px 20px;
                font-size: 13px;
                color: #f0f2f7;
            }
            QMenu::item:selected {
                background-color: #28334f;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #282c37;
                margin: 4px 10px;
            }
            QProgressBar {
                border: none;
                border-radius: 2px;
                background-color: #20242e;
            }
            QProgressBar::chunk {
                border-radius: 2px;
                background-color: #4d7cfe;
            }
            QListWidget {
                border: 1px solid #222632;
                border-radius: 10px;
                background-color: #16181f;
                color: #e2e5eb;
                padding: 6px;
            }
            QListWidget::item {
                border-radius: 8px;
                padding: 8px 10px;
                margin-bottom: 4px;
                background-color: #1a1c24;
                border: 1px solid #222633;
            }
            QListWidget::item:hover {
                background-color: #202430;
                border-color: #2e3547;
            }
            QListWidget::item:selected {
                background-color: #242c3f;
                border: 1px solid #446ad6;
                color: #ffffff;
            }
            """
        )

    def _build_menu(self) -> None:
        settings_menu = self.menuBar().addMenu("Settings")
        tesseract_act = settings_menu.addAction("Configure Tesseract Path...")
        tesseract_act.triggered.connect(self._configure_tesseract_path)
        settings_menu.addSeparator()
        about_act = settings_menu.addAction(f"About {APP_NAME}...")
        about_act.triggered.connect(self._show_about_dialog)

        help_menu = self.menuBar().addMenu("Help")
        about_help = help_menu.addAction(f"About {APP_NAME}...")
        about_help.triggered.connect(self._show_about_dialog)

    def _show_about_dialog(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec()

    def _connect_signals(self) -> None:
        self.search_bar.textChanged.connect(self._on_search_text_changed)
        self.search_bar.returnPressed.connect(self._run_search)
        self.results_list.itemDoubleClicked.connect(self._open_result)
        self.results_list.customContextMenuRequested.connect(self._show_context_menu)
        self.mode_selector.currentIndexChanged.connect(self._on_mode_changed)

    # ------------------------------------------------------------------
    # Indexing triggers (Folder, Drive, Single File)
    # ------------------------------------------------------------------

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Index")
        if folder:
            self._start_indexing(folder=folder)

    def _choose_drive(self) -> None:
        """Let user pick an entire drive/volume to scan/index."""
        available_drives = get_available_drives()

        prompt_title = "Select Drive to Index" if sys.platform == "win32" else "Select Volume / Drive to Index"
        prompt_desc = "Choose a drive or root volume to scan and index:"

        drive, ok = QInputDialog.getItem(
            self,
            prompt_title,
            prompt_desc,
            available_drives,
            0,
            False,
        )
        if ok and drive:
            self._start_indexing(folder=drive)

    def _choose_files(self) -> None:
        """Let user select one or more specific files to index directly."""
        file_filter = (
            "Supported Files (*.pdf *.docx *.xlsx *.xls *.pptx *.odt *.ods *.odp *.rtf *.psd "
            "*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.gif *.txt *.md *.py *.java *.js *.ts "
            "*.html *.css *.json *.csv *.xml *.yaml *.yml *.sql *.c *.cpp *.cs *.php);;"
            "All Files (*.*)"
        )
        files, _ = QFileDialog.getOpenFileNames(self, "Select File(s) to Index", "", file_filter)
        if files:
            self._start_indexing(file_paths=files)

    def _start_indexing(self, folder: Optional[str] = None, file_paths: Optional[List[str]] = None) -> None:
        self.add_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

        if file_paths:
            self.status_label.setText(f"Indexing {len(file_paths)} selected file(s)...")
            worker = IndexerWorker(db_path=DEFAULT_DB_PATH, file_paths=[Path(p) for p in file_paths])
        else:
            self.status_label.setText(f"Scanning {folder}...")
            worker = IndexerWorker(root_folder=folder, db_path=DEFAULT_DB_PATH)

        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.status.connect(self.status_label.setText)
        worker.stats.connect(self._on_index_stats)
        worker.progress.connect(self._on_index_progress)
        worker.finished.connect(self._on_indexing_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._index_thread = thread
        self._index_worker = worker

        thread.start()

    def _on_index_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

    def _on_index_stats(self, stats: dict) -> None:
        self.status_label.setText(
            f"Indexing: {stats['current_file']}  ({stats['scanned']:,} / {stats['total']:,} files — {stats['percent']}%) "
            f"• {stats['files_per_sec']:.1f} files/sec • Rem: {self._format_duration(stats['eta_seconds'])}"
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(int(seconds), 0)
        minutes, secs = divmod(seconds, 60)
        return f"{minutes:02d}:{secs:02d}"

    def _on_indexing_finished(self, summary: dict) -> None:
        self.progress_bar.hide()
        self.add_button.setEnabled(True)

        self._index_thread = None
        self._index_worker = None

        # Reload vector index to include newly written vectors
        self.vector_index = VectorIndex()

        if self.search_bar.text().strip():
            self._run_search()
        else:
            self._refresh_empty_state()

    # ------------------------------------------------------------------
    # Search Execution (Exact, Semantic, Smart/Hybrid)
    # ------------------------------------------------------------------

    def _on_mode_changed(self, _index: int) -> None:
        self.search_mode = self.mode_selector.currentData()
        if self.search_bar.text().strip():
            self._run_search()

    def _on_category_changed(self, category_name: str) -> None:
        self.active_category = category_name
        self._render_current_results()

    def _on_sort_changed(self, _index: int) -> None:
        self.active_sort = self.sort_selector.currentData()
        self._render_current_results()

    def _on_search_text_changed(self, text: str) -> None:
        if text.strip():
            self._search_timer.start()
        else:
            self._search_timer.stop()
            self._all_results = []
            self._refresh_empty_state()

    def _run_search(self) -> None:
        self._search_timer.stop()
        query = self.search_bar.text().strip()

        if not query:
            self._all_results = []
            self._refresh_empty_state()
            return

        if self.search_mode == "hybrid":
            self._all_results = self._search_hybrid(query)
        elif self.search_mode == "semantic":
            self._all_results = self._search_semantic(query)
        else:
            self._all_results = self._search_exact(query)

        self._render_current_results()

    def _search_exact(self, query: str) -> List[Dict[str, Any]]:
        rows = self.db.search(query, limit=None)
        results = []
        for rank, r in enumerate(rows, start=1):
            d = dict(r)
            d["match_type"] = "exact"
            d["relevance_score"] = 1.0 / (rank + 1)
            results.append(d)
        return results

    def _search_semantic(self, query: str) -> List[Dict[str, Any]]:
        if not embeddings.is_semantic_available():
            self._show_message("Semantic search is unavailable. " + embeddings.get_unavailable_reason())
            return []

        query_vector = embeddings.embed_text(query)
        if query_vector is None:
            self._show_message("Semantic search is unavailable.")
            return []

        hits = self.vector_index.search(query_vector, top_k=None)
        if not hits:
            return []

        ids_in_order = [file_id for file_id, _score in hits]
        rows_by_id = self.db.get_files_by_ids(ids_in_order)

        results = []
        for rank, (file_id, score) in enumerate(hits, start=1):
            if file_id in rows_by_id:
                d = dict(rows_by_id[file_id])
                d["match_type"] = "semantic"
                d["relevance_score"] = float(score)
                results.append(d)
        return results

    def _search_hybrid(self, query: str) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion (RRF) combining Exact (FTS5 BM25) and
        Semantic vector search rankings.
        RRF_score = 1 / (60 + rank_exact) + 1 / (60 + rank_semantic)
        """
        K = 60.0
        rrf_scores: Dict[int, float] = {}
        exact_ranks: Dict[int, int] = {}
        semantic_ranks: Dict[int, int] = {}
        file_data: Dict[int, Dict[str, Any]] = {}

        # 1. Exact FTS5 matches (unlimited)
        exact_rows = self.db.search(query, limit=None)
        for rank, r in enumerate(exact_rows, start=1):
            fid = r["id"]
            exact_ranks[fid] = rank
            file_data[fid] = dict(r)
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + (1.0 / (K + rank))

        # 2. Semantic vector matches (unlimited)
        if embeddings.is_semantic_available():
            q_vec = embeddings.embed_text(query)
            if q_vec is not None:
                hits = self.vector_index.search(q_vec, top_k=None)
                if hits:
                    hit_ids = [fid for fid, _ in hits]
                    rows_by_id = self.db.get_files_by_ids(hit_ids)
                    for rank, (fid, _score) in enumerate(hits, start=1):
                        if fid in rows_by_id:
                            semantic_ranks[fid] = rank
                            if fid not in file_data:
                                file_data[fid] = dict(rows_by_id[fid])
                            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + (1.0 / (K + rank))

        if not rrf_scores:
            return []

        # Sort descending by RRF score without any artificial cap
        sorted_ids = sorted(rrf_scores.keys(), key=lambda fid: rrf_scores[fid], reverse=True)

        results = []
        for fid in sorted_ids:
            item = file_data[fid]
            has_exact = fid in exact_ranks
            has_semantic = fid in semantic_ranks

            if has_exact and has_semantic:
                item["match_type"] = "hybrid_both"
            elif has_semantic:
                item["match_type"] = "semantic"
            else:
                item["match_type"] = "exact"

            item["relevance_score"] = rrf_scores[fid]
            results.append(item)

        return results

    # ------------------------------------------------------------------
    # Results Rendering, Filtering & Sorting
    # ------------------------------------------------------------------

    def _render_current_results(self) -> None:
        self.results_list.clear()

        if not self._all_results:
            if self.search_bar.text().strip():
                self._show_message("No results found.")
            else:
                self._refresh_empty_state()
            self.count_badge.setText("")
            return

        # 1. Category Filtering
        valid_extensions = CATEGORIES.get(self.active_category)
        if valid_extensions is not None:
            filtered = [r for r in self._all_results if r.get("extension", "").lower() in valid_extensions]
        else:
            filtered = list(self._all_results)

        # 2. Sorting
        if self.active_sort == "newest":
            filtered.sort(key=lambda r: r.get("modified_at", 0) or 0, reverse=True)
        elif self.active_sort == "oldest":
            filtered.sort(key=lambda r: r.get("modified_at", 0) or 0)
        elif self.active_sort == "largest":
            filtered.sort(key=lambda r: r.get("file_size", 0) or 0, reverse=True)
        elif self.active_sort == "smallest":
            filtered.sort(key=lambda r: r.get("file_size", 0) or 0)
        elif self.active_sort == "name":
            filtered.sort(key=lambda r: (r.get("filename", "") or "").lower())
        # "relevance" retains existing ranked order

        if not filtered:
            self._show_message(f"No results found in '{self.active_category}'.")
            self.count_badge.setText(f"0 / {len(self._all_results)}")
            return

        for row in filtered:
            item = self._build_result_item(row)
            self.results_list.addItem(item)

        total_all = len(self._all_results)
        shown_count = len(filtered)
        if self.active_category == "All":
            self.count_badge.setText(f"{shown_count} match{'es' if shown_count != 1 else ''}")
        else:
            self.count_badge.setText(f"{shown_count} of {total_all} in {self.active_category}")

        self.results_label.hide()
        self.results_list.show()

    def _build_result_item(self, row: Dict[str, Any]) -> QListWidgetItem:
        ext = row.get("extension", "").lower()
        is_image = ext in IMAGE_EXTENSIONS
        is_psd = ext == ".psd"
        match_type = row.get("match_type", "exact")

        size_str = format_file_size(row.get("file_size", 0))
        date_str = format_timestamp(row.get("modified_at", 0))

        # Title line
        header = f"{row.get('filename', 'Unknown')}  •  {size_str}  •  {date_str}"
        path_line = row.get("full_path", "")

        # Badge tag
        if match_type == "hybrid_both":
            badge = "[✨ Exact & Semantic Match]"
        elif match_type == "semantic":
            badge = "[🧠 Semantic Match]"
        elif is_image:
            badge = "[🔍 Matched via OCR Text]"
        elif is_psd:
            badge = "[🎨 Photoshop Document]"
        else:
            badge = "[📄 Exact Match]"

        item_text = f"{header}\n{path_line}\n{badge}"

        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, row)

        if is_image:
            item.setIcon(self._get_thumbnail_icon(row["full_path"]))

        return item

    def _get_thumbnail_icon(self, full_path: str) -> QIcon:
        cached = self._thumbnail_cache.get(full_path)
        if cached is not None:
            return cached

        pixmap = safe_load_thumbnail(full_path, max_size=(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        if pixmap is not None and not pixmap.isNull():
            icon = QIcon(pixmap)
        else:
            icon = self.windowIcon()

        self._thumbnail_cache[full_path] = icon
        return icon

    def _show_message(self, message: str) -> None:
        self.results_list.clear()
        self.results_list.hide()
        self.results_label.setText(message)
        self.results_label.show()

    def _refresh_empty_state(self) -> None:
        self.results_list.hide()
        count = self.db.count_files()
        if count == 0:
            self.results_label.setText("No files indexed yet. Click '+ Index / Add' above to get started.")
        else:
            self.results_label.setText(f"Indexed: {count:,} files. Start typing above to search.")
        self.results_label.show()
        self.count_badge.setText("")

    # ------------------------------------------------------------------
    # Right-Click Context Menu & Actions
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.results_list.itemAt(pos)
        if not item:
            return

        row = item.data(Qt.UserRole)
        if not row:
            return

        full_path = row.get("full_path", "")
        filename = row.get("filename", "")

        menu = QMenu(self)

        act_open = menu.addAction("📂  Open File")
        act_open.triggered.connect(lambda: self._open_file_path(full_path))

        explorer_label = "📁  Show in Finder" if sys.platform == "darwin" else "📁  Show in File Manager"
        act_explorer = menu.addAction(explorer_label)
        act_explorer.triggered.connect(lambda: self._reveal_in_explorer(full_path))

        menu.addSeparator()

        act_copy_path = menu.addAction("📋  Copy Path")
        act_copy_path.triggered.connect(lambda: QApplication.clipboard().setText(full_path))

        act_copy_name = menu.addAction("📝  Copy File Name")
        act_copy_name.triggered.connect(lambda: QApplication.clipboard().setText(filename))

        act_copy_text = menu.addAction("📄  Copy Extracted Content")
        act_copy_text.triggered.connect(lambda: self._copy_extracted_text(full_path))

        menu.addSeparator()

        act_reindex = menu.addAction("🔄  Re-index this File")
        act_reindex.triggered.connect(lambda: self._start_indexing(file_paths=[full_path]))

        menu.exec(self.results_list.mapToGlobal(pos))

    def _open_result(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.UserRole)
        if row and "full_path" in row:
            self._open_file_path(row["full_path"])

    def _open_file_path(self, full_path: str) -> None:
        if full_path and os.path.exists(full_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(full_path))
        else:
            self.status_label.setText(f"File not found on disk: {full_path}")

    def _reveal_in_explorer(self, full_path: str) -> None:
        reveal_in_file_manager(full_path)

    def _copy_extracted_text(self, full_path: str) -> None:
        content = self.db.get_file_content(full_path)
        if content:
            QApplication.clipboard().setText(content)
            self.status_label.setText(f"Copied extracted content of {os.path.basename(full_path)} to clipboard.")
        else:
            self.status_label.setText(f"No extracted text stored for {os.path.basename(full_path)}.")

    # ------------------------------------------------------------------
    # Settings & Shutdown
    # ------------------------------------------------------------------

    def _configure_tesseract_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Tesseract Executable")
        if path:
            ocr.configure_tesseract_path(path)
            self._update_ocr_status()

    def _update_ocr_status(self) -> None:
        if not ocr.is_ocr_available():
            self.status_label.setText(ocr.get_unavailable_reason())

    def closeEvent(self, event) -> None:
        self.db.close()
        super().closeEvent(event)
