"""
core/database.py

All SQLite access for LifeSearch lives here: schema creation (files
table + FTS5 index), inserts/updates, deletion cleanup, and search
queries. No SQL should appear anywhere outside this file.

sqlite3 connections are not safe to share across threads, so each
piece of code that needs the database (the UI thread, the indexing
worker thread) creates its own Database instance pointed at the same
file. WAL mode is enabled so a background write and a foreground read
can happen without locking each other out.

V4 adds semantic-search bookkeeping columns (content_hash,
embedding_status, embedded_at) to the existing `files` table rather
than a separate table - a file's embedding state is 1:1 with the file
row, and a file's own primary key `id` doubles as its vector ID in
core/vector_index.py (the simplest reliable file_id <-> vector_id
mapping - see that module for details). The existing files/files_fts
tables and exact search are untouched by this.
"""

import hashlib
import sqlite3
from pathlib import Path
from typing import Optional

# data/lifesearch.db, resolved relative to the project root regardless
# of the current working directory the app was launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(PROJECT_ROOT / "data" / "lifesearch.db")

# embedding_status values:
#   'pending' - never attempted (new row, or semantic search unavailable
#               at the time it was indexed)
#   'done'    - content_hash reflects the currently-embedded content
#   'failed'  - embedding was attempted and raised; will be retried
#               next time this file is seen (id remains 'failed' until
#               a run succeeds or the content changes again)


def content_hash(content: str) -> str:
    """Stable hash of a file's extracted content, used to decide
    whether an embedding needs to be regenerated. Not a security hash
    - just cheap change-detection for potentially large text blobs."""
    return hashlib.sha256((content or "").encode("utf-8", errors="replace")).hexdigest()


class Database:
    """Thin wrapper around the LifeSearch SQLite database."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                full_path        TEXT UNIQUE NOT NULL,
                filename         TEXT NOT NULL,
                extension        TEXT NOT NULL,
                file_size        INTEGER NOT NULL,
                modified_at      REAL NOT NULL,
                content          TEXT,
                source_type      TEXT NOT NULL DEFAULT 'text',
                content_hash     TEXT,
                embedding_status TEXT NOT NULL DEFAULT 'pending',
                embedded_at      REAL
            );

            -- External-content FTS5 index: the actual text lives in
            -- `files`, this table only stores the search index itself.
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                filename,
                content,
                content='files',
                content_rowid='id'
            );

            -- Keep files_fts in sync with files automatically, no
            -- matter which code path writes to `files`.
            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, filename, content)
                VALUES (new.id, new.filename, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, filename, content)
                VALUES ('delete', old.id, old.filename, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, filename, content)
                VALUES ('delete', old.id, old.filename, old.content);
                INSERT INTO files_fts(rowid, filename, content)
                VALUES (new.id, new.filename, new.content);
            END;
            """
        )
        self._migrate_add_columns()
        self._conn.commit()

    def _migrate_add_columns(self) -> None:
        """
        Databases created by earlier versions won't have every column
        in the CREATE TABLE above, since it wasn't there when their
        table was first created. ALTER TABLE ... ADD COLUMN is safe on
        an existing table with data, so run any missing ones once
        rather than requiring users to delete their existing
        lifesearch.db (which, per V3.1, may hold ~5,000 already-OCR'd
        files worth re-reading only if we have to).
        """
        existing_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(files)")
        }
        migrations = {
            "source_type": "TEXT NOT NULL DEFAULT 'text'",
            "content_hash": "TEXT",
            "embedding_status": "TEXT NOT NULL DEFAULT 'pending'",
            "embedded_at": "REAL",
        }
        for column, definition in migrations.items():
            if column not in existing_columns:
                self._conn.execute(f"ALTER TABLE files ADD COLUMN {column} {definition}")

    def get_existing(self, full_path: str) -> Optional[sqlite3.Row]:
        """
        Return the existing row for a path (id, file_size,
        modified_at, content, content_hash, embedding_status), or None
        if it has never been indexed. `content` is included so callers
        can back-fill a missing embedding for an on-disk-unchanged file
        WITHOUT re-reading or re-OCRing it from disk - the previously
        extracted text is already sitting right here.
        """
        cur = self._conn.execute(
            """
            SELECT id, file_size, modified_at, content, content_hash, embedding_status
            FROM files WHERE full_path = ?
            """,
            (full_path,),
        )
        return cur.fetchone()

    def upsert_file(
        self,
        full_path: str,
        filename: str,
        extension: str,
        file_size: int,
        modified_at: float,
        content: str,
        source_type: str = "text",
    ) -> int:
        """
        Insert a new file record, or update it in place if full_path
        already exists (full_path is the unique key). Returns the
        row's id (== its vector_id in the semantic index).

        Content changing always resets embedding_status back to
        'pending' so the semantic-indexing phase knows to regenerate
        it; metadata-only re-saves of identical content are avoided by
        callers checking change-detection before calling this at all.

        source_type distinguishes how `content` was obtained: 'text'
        for files read directly, 'ocr' for text extracted from an
        image via core.ocr. Either way it's stored in the same
        `content` column and indexed by the same FTS5 table, so exact
        search doesn't need to know or care which kind it's matching.
        """
        self._conn.execute(
            """
            INSERT INTO files (full_path, filename, extension, file_size, modified_at, content, source_type, embedding_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(full_path) DO UPDATE SET
                filename         = excluded.filename,
                extension        = excluded.extension,
                file_size        = excluded.file_size,
                modified_at      = excluded.modified_at,
                content          = excluded.content,
                source_type      = excluded.source_type,
                embedding_status = 'pending',
                content_hash     = NULL
            """,
            (full_path, filename, extension, file_size, modified_at, content, source_type),
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM files WHERE full_path = ?", (full_path,)
        ).fetchone()["id"]

    def search(self, query: str, limit: Optional[int] = None) -> list[sqlite3.Row]:
        """
        Exact full-text search across filename + content (which holds
        either raw file text or OCR-extracted image text), ranked by
        relevance (bm25). Returns [] for blank input or malformed
        queries rather than raising. If limit is None or <= 0, returns all matches.
        """
        query = query.strip()
        if not query:
            return []

        fts_query = self._build_fts_query(query)

        try:
            if limit is not None and limit > 0:
                cur = self._conn.execute(
                    """
                    SELECT f.id, f.full_path, f.filename, f.extension, f.source_type, f.file_size, f.modified_at
                    FROM files_fts
                    JOIN files f ON f.id = files_fts.rowid
                    WHERE files_fts MATCH ?
                    ORDER BY bm25(files_fts)
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )
            else:
                cur = self._conn.execute(
                    """
                    SELECT f.id, f.full_path, f.filename, f.extension, f.source_type, f.file_size, f.modified_at
                    FROM files_fts
                    JOIN files f ON f.id = files_fts.rowid
                    WHERE files_fts MATCH ?
                    ORDER BY bm25(files_fts)
                    """,
                    (fts_query,),
                )
            return cur.fetchall()
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """
        Turn raw user input into a safe FTS5 MATCH expression by
        quoting each whitespace-separated term individually. This
        stops characters like '.', '-', '(' in the search box from
        being interpreted as FTS5 query syntax and erroring out.
        """
        terms = query.split()
        quoted = ['"{}"'.format(t.replace('"', '""')) for t in terms]
        return " ".join(quoted)

    def count_files(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM files")
        return cur.fetchone()["n"]

    # ------------------------------------------------------------------
    # V4: deleted-file cleanup
    # ------------------------------------------------------------------

    def get_paths_under_root(self, root_path: str) -> list[sqlite3.Row]:
        """
        Return (id, full_path) for every indexed file whose path is
        inside root_path. Used by the indexer, after a scan, to find
        previously-indexed files that no longer exist on disk (deleted,
        moved out, or now excluded) so they can be purged.
        """
        # full_path is stored resolved/absolute; a trailing separator
        # keeps "/foo/bar" from matching a sibling "/foo/barbaz".
        prefix = root_path.rstrip("/\\") + "/"
        cur = self._conn.execute(
            "SELECT id, full_path FROM files WHERE full_path = ? OR full_path LIKE ? ESCAPE '\\'",
            (root_path, prefix.replace("%", "\\%").replace("_", "\\_") + "%"),
        )
        return cur.fetchall()

    def delete_files(self, file_ids: list[int]) -> None:
        """
        Remove file records by id. The files_ad trigger removes the
        matching files_fts row automatically. Callers are responsible
        for also removing these ids from the vector index (this class
        doesn't know about core.vector_index, to keep it storage-only).
        """
        if not file_ids:
            return
        placeholders = ",".join("?" for _ in file_ids)
        self._conn.execute(f"DELETE FROM files WHERE id IN ({placeholders})", file_ids)
        self._conn.commit()

    # ------------------------------------------------------------------
    # V4: semantic embedding bookkeeping
    # ------------------------------------------------------------------

    def get_files_needing_embedding(self) -> list[sqlite3.Row]:
        """
        Return (id, content) for every file whose embedding is missing
        or stale: embedding_status != 'done', or content_hash doesn't
        match a freshly computed hash of its current content. Skips
        rows with no content (nothing to embed).

        This is what makes semantic indexing incremental: a file whose
        on-disk content hasn't changed since it was last embedded is
        never re-embedded, even across app restarts - only new files,
        changed files, or files that have never been embedded show up
        here.
        """
        cur = self._conn.execute(
            """
            SELECT id, content, content_hash, embedding_status
            FROM files
            WHERE content IS NOT NULL AND content != ''
              AND (embedding_status != 'done' OR content_hash IS NULL)
            """
        )
        rows = cur.fetchall()
        return [
            row for row in rows
            if row["embedding_status"] != "done"
            or row["content_hash"] != content_hash(row["content"])
        ]

    def mark_embedded(self, file_id: int, hash_value: str, embedded_at: float) -> None:
        self._conn.execute(
            "UPDATE files SET embedding_status = 'done', content_hash = ?, embedded_at = ? WHERE id = ?",
            (hash_value, embedded_at, file_id),
        )
        self._conn.commit()

    def mark_embedding_failed(self, file_id: int) -> None:
        self._conn.execute(
            "UPDATE files SET embedding_status = 'failed' WHERE id = ?",
            (file_id,),
        )
        self._conn.commit()

    def get_files_by_ids(self, file_ids: list[int]) -> dict[int, sqlite3.Row]:
        """Return {id: row(id, full_path, filename, extension, source_type, file_size, modified_at)}
        for a list of ids, used to map semantic search vector results
        (which only know ids) back to displayable file info."""
        if not file_ids:
            return {}
        placeholders = ",".join("?" for _ in file_ids)
        cur = self._conn.execute(
            f"SELECT id, full_path, filename, extension, source_type, file_size, modified_at FROM files WHERE id IN ({placeholders})",
            file_ids,
        )
        return {row["id"]: row for row in cur.fetchall()}

    def get_file_content(self, target: int | str) -> Optional[str]:
        """Retrieve extracted text content for a file by its id or full_path."""
        if isinstance(target, int):
            cur = self._conn.execute("SELECT content FROM files WHERE id = ?", (target,))
        else:
            cur = self._conn.execute("SELECT content FROM files WHERE full_path = ?", (str(target),))
        row = cur.fetchone()
        return row["content"] if row else None

    def close(self) -> None:
        self._conn.close()
