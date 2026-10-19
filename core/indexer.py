"""
core/indexer.py

Coordinates local file indexing: walks a folder (core.scanner), routes
each file to the right extraction pipeline - plain read, a document
parser (core.extractors), or local OCR (core.ocr) - writes/updates
records in SQLite (core.database), keeps the local semantic vector
index (core.vector_index + core.embeddings) up to date, and cleans up
records for files that no longer exist on disk.

IndexerWorker is a QObject meant to be moved to a QThread by the UI
(see ui/main_window.py) so scanning, extraction, OCR, and embedding
never block the Qt event loop. It talks back to the UI only through
signals - it never touches any UI widgets directly. Everything below
runs on a single background thread; this does not spawn additional
threads for extraction or embedding.

Run phases, in order:
  1. Scan the folder for current supported files (text/code/config,
     documents, images).
  2. Deletion cleanup - remove DB + FTS5 + vector-index entries for
     any previously-indexed file under this folder that's no longer
     on disk.
  3. Per-file extraction + upsert: plain text/code read directly,
     richer documents (PDF/Office/OpenDocument/RTF) parsed via
     core.extractors, images OCR'd via core.ocr. Unchanged files
     (same size + mtime as last run) are skipped entirely - no
     re-read, no re-parse, no re-OCR. A changed/new file's upsert
     resets its embedding_status so step 4 catches it.
  4. Semantic indexing - embed any file (anywhere in the database, not
     just this folder) whose embedding is missing or stale. This is
     what backfills embeddings for files indexed before semantic
     search existed, using their already-stored content - no disk
     re-read, no re-parse, no re-OCR.

Throughout, ETA/progress statistics are computed from a rolling window
of recent per-file timestamps (see _emit_stats) rather than a single
instantaneous rate, so the displayed "files/sec" and "estimated
remaining" don't jump around on every file.
"""

import time
from collections import deque
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core import embeddings, ocr
from core.database import Database, content_hash
from core.extractors import extract_text
from core.scanner import IMAGE_EXTENSIONS, get_extension, scan_directory

# No file size limits - index documents, PSDs, and images of any size.
MAX_DOCUMENT_SIZE_BYTES = None
MAX_IMAGE_FILE_SIZE_BYTES = None
MAX_PSD_SIZE_BYTES = None

# How many texts to embed per model.encode() call - batching is much
# faster than embedding one string at a time.
EMBEDDING_BATCH_SIZE = 32

# How often (seconds) to emit a `stats` update during the per-file
# loop - frequent enough to feel real-time, not so frequent that it
# floods the Qt event queue on a fast local disk with thousands of tiny
# files.
STATS_EMIT_INTERVAL_SECONDS = 0.15

# Rolling window size (in files) used to smooth the files/sec estimate.
# Too small -> jumpy ETA; too large -> slow to react to a real slowdown
# (e.g. hitting a folder of huge PDFs after many small text files).
SPEED_WINDOW_SIZE = 40


class IndexerWorker(QObject):
    """Background worker that scans a folder, indexes supported files,
    cleans up deleted ones, and keeps the semantic index in sync."""

    status = Signal(str)                  # short human-readable status text
    progress = Signal(int, int)           # (files_processed, files_total) - scan/extract/OCR phase
    stats = Signal(dict)                  # rich progress dict - see _emit_stats
    semantic_progress = Signal(int, int)  # (embedded_so_far, embedding_total)
    finished = Signal(dict)               # summary dict - see run()'s final emit

    def __init__(
        self,
        root_folder: Optional[str] = None,
        db_path: str = "",
        file_paths: Optional[list[Path | str]] = None,
    ) -> None:
        super().__init__()
        self.root_folder = root_folder
        self.db_path = db_path
        self.explicit_file_paths = file_paths

    def run(self) -> None:
        """Entry point executed on the worker thread."""
        from core.vector_index import VectorIndex  # local import: keeps a heavy
        # optional dependency (faiss) out of the import graph for any
        # code path that never actually indexes.

        start_time = time.time()

        # sqlite3 connections must not be shared across threads, so
        # this worker opens its own, pointed at the same file the UI
        # thread's Database instance uses. Same reasoning for its own
        # VectorIndex instance - see core/vector_index.py.
        db = Database(self.db_path)
        vector_index = VectorIndex()

        ocr_available = ocr.is_ocr_available()
        semantic_available = embeddings.is_semantic_available()

        deleted_count = 0
        if self.explicit_file_paths is not None:
            self.status.emit("Preparing files to index...")
            file_paths = [Path(p) for p in self.explicit_file_paths if Path(p).is_file()]
        else:
            self.status.emit("Scanning...")
            try:
                file_paths = list(scan_directory(self.root_folder))
            except OSError as exc:
                self.status.emit(f"Could not scan: {exc}")
                self.finished.emit(self._summary(0, 0, 0, 0, 0, 0, 0, start_time))
                db.close()
                return

            # --- Deletion cleanup (runs even if the folder now has 0
            # supported files - that's exactly the "everything in here was
            # deleted" case) ---
            resolved_root = str(Path(self.root_folder).resolve())
            current_full_paths = {str(p.resolve()) for p in file_paths}
            deleted_count = self._cleanup_deleted_files(
                db, vector_index, resolved_root, current_full_paths
            )

        total = len(file_paths)
        indexed = 0
        skipped = 0
        errors = 0
        ocr_processed = 0

        if total == 0:
            message = "No supported files found in this folder."
            if deleted_count:
                message += f" Removed {deleted_count} deleted file(s) from the index."
            self.status.emit(message)
        else:
            if not ocr_available:
                self.status.emit(
                    "Note: OCR unavailable this run - images will be indexed "
                    "by filename only. " + ocr.get_unavailable_reason()
                )

            recent_timestamps: deque = deque(maxlen=SPEED_WINDOW_SIZE)
            last_stats_emit = 0.0

            for i, path in enumerate(file_paths, start=1):
                ext = get_extension(path.name)
                is_image = ext in IMAGE_EXTENSIONS

                try:
                    if is_image:
                        result, did_ocr = self._index_image(db, path, ext, ocr_available)
                        if did_ocr:
                            ocr_processed += 1
                    else:
                        result = self._index_document(db, path, ext)

                    if result:
                        indexed += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    # A single bad file (odd permissions, corrupt
                    # document, race condition where it gets deleted
                    # mid-scan, extraction/OCR failure, etc.) must
                    # never take down the whole indexing run.
                    print(f"LifeSearch: failed to index {path}: {exc}")
                    errors += 1

                self.progress.emit(i, total)

                now = time.time()
                recent_timestamps.append(now)
                if now - last_stats_emit >= STATS_EMIT_INTERVAL_SECONDS or i == total:
                    self._emit_stats(path.name, i, total, start_time, recent_timestamps)
                    last_stats_emit = now

        # --- Semantic indexing phase ---
        # Always runs, even when this particular scan found 0 files
        # (e.g. re-scanning an already-fully-indexed folder while
        # OTHER files elsewhere in the database still need
        # backfilling). Deliberately queries the WHOLE database
        # (db.get_files_needing_embedding), not just files touched in
        # this run.
        embeddings_generated = 0
        if semantic_available:
            embeddings_generated = self._build_semantic_index(db, vector_index)
        else:
            pending = len(db.get_files_needing_embedding())
            if pending:
                self.status.emit(
                    f"Semantic search unavailable - {pending} file(s) will be "
                    "embedded once it's available. " + embeddings.get_unavailable_reason()
                )

        vector_index.save()
        db.close()

        elapsed = time.time() - start_time
        final_message = (
            f"Indexing complete\n"
            f"Elapsed: {self._format_duration(elapsed)}\n"
            f"Discovered: {total}   Indexed: {indexed}   Skipped: {skipped}   Errors: {errors}\n"
            f"OCR processed: {ocr_processed}   Embeddings generated: {embeddings_generated}"
        )
        if deleted_count:
            final_message += f"\nRemoved {deleted_count} deleted file(s) from the index."
        self.status.emit(final_message)

        self.finished.emit(self._summary(
            indexed, skipped, errors, ocr_processed, embeddings_generated,
            deleted_count, total, start_time,
        ))

    # ------------------------------------------------------------------
    # ETA / stats
    # ------------------------------------------------------------------

    def _emit_stats(
        self,
        current_file: str,
        scanned: int,
        total: int,
        start_time: float,
        recent_timestamps: deque,
    ) -> None:
        """
        Emit a rich progress dict for the UI's ETA display. Speed is a
        rolling average over the last SPEED_WINDOW_SIZE files (not a
        single instantaneous delta), which is what keeps the
        files/sec and estimated-remaining numbers from jumping around
        file to file.
        """
        elapsed = time.time() - start_time

        if len(recent_timestamps) >= 2:
            span = recent_timestamps[-1] - recent_timestamps[0]
            files_per_sec = (len(recent_timestamps) - 1) / span if span > 0 else 0.0
        else:
            files_per_sec = scanned / elapsed if elapsed > 0 else 0.0

        remaining_files = max(total - scanned, 0)
        eta_seconds = remaining_files / files_per_sec if files_per_sec > 0 else 0.0

        self.stats.emit({
            "current_file": current_file,
            "scanned": scanned,
            "total": total,
            "percent": int(scanned / total * 100) if total else 100,
            "files_per_sec": files_per_sec,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta_seconds,
        })

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(int(seconds), 0)
        minutes, secs = divmod(seconds, 60)
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _summary(
        indexed: int, skipped: int, errors: int, ocr_processed: int,
        embeddings_generated: int, deleted_count: int, total_discovered: int,
        start_time: float,
    ) -> dict:
        return {
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
            "ocr_processed": ocr_processed,
            "embeddings_generated": embeddings_generated,
            "deleted_count": deleted_count,
            "total_discovered": total_discovered,
            "elapsed_seconds": time.time() - start_time,
        }

    # ------------------------------------------------------------------
    # Deletion cleanup
    # ------------------------------------------------------------------

    def _cleanup_deleted_files(
        self,
        db: Database,
        vector_index,
        resolved_root: str,
        current_full_paths: set,
    ) -> int:
        """
        Find files previously indexed under resolved_root that are no
        longer present in this scan's results (deleted, moved away, or
        now excluded), and remove them from SQLite (which cascades to
        FTS5 via trigger) and from the vector index. Returns the
        number removed.
        """
        previously_indexed = db.get_paths_under_root(resolved_root)
        deleted_ids = [
            row["id"] for row in previously_indexed
            if row["full_path"] not in current_full_paths
        ]

        if not deleted_ids:
            return 0

        self.status.emit(f"Removing {len(deleted_ids)} deleted file(s) from the index...")
        db.delete_files(deleted_ids)          # cascades to files_fts via trigger
        vector_index.remove_ids(deleted_ids)  # vector genuinely gone, not just hidden
        return len(deleted_ids)

    # ------------------------------------------------------------------
    # Text / documents (unified via core.extractors)
    # ------------------------------------------------------------------

    def _index_document(self, db: Database, path: Path, extension: str) -> bool:
        """
        Index a single non-image file - plain text/code or a richer
        document (PDF/Office/OpenDocument/RTF), dispatched by
        core.extractors.extract_text based on `extension`. Returns
        True if it was (re)extracted and written to the database,
        False if it was skipped - unchanged since last index,
        unreadable, unsupported (e.g. legacy .doc/.ppt), or too large.
        A successful write resets embedding_status to 'pending' (see
        Database.upsert_file), which is what queues it for the
        semantic phase later in this same run.
        """
        stat_result = self._safe_stat(path)
        if stat_result is None:
            return False
        file_size, modified_at, full_path = stat_result

        existing = db.get_existing(full_path)
        if self._is_unchanged(existing, file_size, modified_at):
            return False

        content = extract_text(path, extension)
        if content is None:
            # Unreadable, corrupt, or an intentionally-unsupported
            # format (legacy .doc/.ppt) - still worth a record so the
            # file is at least findable by name, consistent with how
            # an image with failed/unavailable OCR is handled.
            content = ""

        db.upsert_file(
            full_path=full_path,
            filename=path.name,
            extension=extension,
            file_size=file_size,
            modified_at=modified_at,
            content=content,
            source_type="text",
        )
        return True

    # ------------------------------------------------------------------
    # Images (OCR)
    # ------------------------------------------------------------------

    def _index_image(
        self, db: Database, path: Path, extension: str, ocr_available: bool
    ) -> tuple:
        """
        Index a single image. If OCR is available, run it and store
        the extracted text so the image becomes searchable by its
        visible content (both exact FTS5 and, later in this run,
        semantic search). If OCR isn't available, the image is still
        indexed (searchable by filename), just without OCR text.

        Returns (indexed: bool, did_run_ocr: bool). indexed is False
        only when the image was skipped as unchanged since last index
        - did_run_ocr tracks the "OCR processed" statistic separately,
        since an image can be indexed without OCR having run (OCR
        unavailable) or can fail OCR while still being indexed.
        """
        stat_result = self._safe_stat(path)
        if stat_result is None:
            return False, False
        file_size, modified_at, full_path = stat_result

        existing = db.get_existing(full_path)
        if self._is_unchanged(existing, file_size, modified_at):
            return False, False  # unchanged - don't re-run OCR

        content = ""
        did_run_ocr = False
        if ocr_available:
            did_run_ocr = True
            extracted = ocr.extract_text_from_image(path)
            if extracted:
                content = extracted
            else:
                # OCR ran but found/extracted nothing usable - could be
                # a blank image, an unsupported/corrupt file, or a
                # genuine OCR failure. Log it; the image is still
                # indexed below (searchable by filename) rather than
                # dropped entirely.
                print(f"LifeSearch: OCR produced no text for {path}")

        db.upsert_file(
            full_path=full_path,
            filename=path.name,
            extension=extension,
            file_size=file_size,
            modified_at=modified_at,
            content=content,
            source_type="ocr",
        )
        return True, did_run_ocr

    # ------------------------------------------------------------------
    # Semantic indexing
    # ------------------------------------------------------------------

    def _build_semantic_index(self, db: Database, vector_index) -> int:
        """
        Embed every file whose embedding is missing or stale
        (db.get_files_needing_embedding already encodes the
        incremental rule: new, changed, or never-embedded content
        only) and write the vectors into vector_index. Runs in batches
        for throughput; emits progress after each file so the UI can
        show "Building semantic index: N / total". Returns the number
        of files successfully embedded this run.
        """
        rows = db.get_files_needing_embedding()
        total = len(rows)
        if total == 0:
            return 0

        self.status.emit(f"Building semantic index: 0 / {total}")
        done = 0
        succeeded = 0

        for batch_start in range(0, total, EMBEDDING_BATCH_SIZE):
            batch = rows[batch_start:batch_start + EMBEDDING_BATCH_SIZE]
            texts = [row["content"] for row in batch]

            try:
                vectors = embeddings.embed_texts(texts)
            except Exception as exc:
                print(f"LifeSearch: batch embedding failed: {exc}")
                vectors = None

            if vectors is None:
                # The model died mid-run (or was never available) -
                # mark this batch failed so it's retried next time
                # rather than silently staying 'pending' forever with
                # no visibility. FTS5/exact search is entirely
                # unaffected either way.
                for row in batch:
                    db.mark_embedding_failed(row["id"])
                    done += 1
                self.status.emit(f"Building semantic index: {done} / {total}")
                self.semantic_progress.emit(done, total)
                continue

            for row, vector in zip(batch, vectors):
                try:
                    vector_index.add_or_update(row["id"], vector)
                    db.mark_embedded(row["id"], content_hash(row["content"]), time.time())
                    succeeded += 1
                except Exception as exc:
                    print(f"LifeSearch: failed to embed file id {row['id']}: {exc}")
                    db.mark_embedding_failed(row["id"])
                done += 1

            self.status.emit(f"Building semantic index: {done} / {total}")
            self.semantic_progress.emit(done, total)

        return succeeded

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_stat(path: Path) -> Optional[tuple]:
        """Return (file_size, modified_at, full_path) or None if the
        file can't be stat'd (deleted/locked between scan and read)."""
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime, str(path.resolve())

    @staticmethod
    def _is_unchanged(existing, file_size: int, modified_at: float) -> bool:
        return (
            existing is not None
            and existing["file_size"] == file_size
            and existing["modified_at"] == modified_at
        )
