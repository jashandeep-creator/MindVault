"""
tests/test_features.py

Tests for:
  - Utility helpers (drive detection, size formatting, timestamp formatting)
  - Database metadata retrieval and get_file_content
  - IndexerWorker with explicit single/multi-file paths
  - Hybrid search Reciprocal Rank Fusion (RRF) logic
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

from core.database import Database
from core.indexer import IndexerWorker
from utils import format_file_size, format_timestamp, get_available_drives


class TestUtilities:
    def test_get_available_drives(self):
        drives = get_available_drives()
        assert isinstance(drives, list)
        assert len(drives) >= 1
        for d in drives:
            assert os.path.exists(d)

    def test_format_file_size(self):
        assert format_file_size(0) == "0 B"
        assert format_file_size(500) == "500 B"
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(2048) == "2.0 KB"
        assert format_file_size(1024 * 1024 * 2) == "2.0 MB"
        assert format_file_size(1024 * 1024 * 1024 * 3) == "3.00 GB"

    def test_format_timestamp(self):
        assert format_timestamp(0) == "Unknown date"
        assert format_timestamp(None) == "Unknown date"
        formatted = format_timestamp(1700000000.0)
        assert "2023" in formatted

    def test_app_metadata(self):
        from utils import APP_AUTHOR, APP_NAME, APP_VERSION
        assert APP_NAME == "MindVault"
        assert "Jashandeep" in APP_AUTHOR
        assert APP_VERSION.startswith("1.")

    def test_reveal_in_file_manager(self, tmp_path):
        from utils import reveal_in_file_manager
        sample = tmp_path / "test.txt"
        sample.write_text("hello", encoding="utf-8")
        # Should execute safely without raising exceptions
        reveal_in_file_manager(str(sample))
        reveal_in_file_manager("")



class TestDatabaseFeatures:
    @pytest.fixture
    def test_db(self, tmp_path):
        db_path = str(tmp_path / "test_lifesearch.db")
        db = Database(db_path)
        yield db
        db.close()

    def test_get_file_content_and_metadata(self, test_db, tmp_path):
        sample_file = tmp_path / "invoice_sample.txt"
        sample_file.write_text("Invoice #98234 for consulting services", encoding="utf-8")

        fid = test_db.upsert_file(
            full_path=str(sample_file),
            filename=sample_file.name,
            extension=sample_file.suffix,
            file_size=120,
            modified_at=time.time(),
            content="Invoice #98234 for consulting services",
            source_type="text",
        )

        # Test content retrieval by id and by path
        content_by_id = test_db.get_file_content(fid)
        content_by_path = test_db.get_file_content(str(sample_file))
        assert content_by_id == "Invoice #98234 for consulting services"
        assert content_by_path == "Invoice #98234 for consulting services"

        # Test search returns metadata columns: id, file_size, modified_at
        results = test_db.search("Invoice")
        assert len(results) == 1
        row = results[0]
        assert row["id"] == fid
        assert row["file_size"] == 120
        assert row["filename"] == "invoice_sample.txt"

        # Test get_files_by_ids returns metadata
        mapped = test_db.get_files_by_ids([fid])
        assert fid in mapped
        assert mapped[fid]["file_size"] == 120


class TestIndexerExplicitFiles:
    def test_index_single_file_without_deleting_neighbors(self, tmp_path):
        db_path = str(tmp_path / "index_test.db")
        db = Database(db_path)

        folder = tmp_path / "workspace"
        folder.mkdir()
        file1 = folder / "doc1.txt"
        file1.write_text("First document content", encoding="utf-8")
        file2 = folder / "doc2.txt"
        file2.write_text("Second document content", encoding="utf-8")

        # Step 1: Index file 1 explicitly
        worker1 = IndexerWorker(db_path=db_path, file_paths=[file1])
        worker1.run()

        assert db.count_files() == 1
        res1 = db.search("First")
        assert len(res1) == 1

        # Step 2: Index file 2 explicitly - file 1 must NOT be deleted!
        worker2 = IndexerWorker(db_path=db_path, file_paths=[file2])
        worker2.run()

        assert db.count_files() == 2
        res2 = db.search("Second")
        assert len(res2) == 1
        # Check file 1 is still present
        res1_after = db.search("First")
        assert len(res1_after) == 1

        db.close()


class TestUnlimitedFeatures:
    def test_unlimited_search_results(self, tmp_path):
        db_path = str(tmp_path / "unlimited_db.db")
        db = Database(db_path)

        # Insert 75 matching rows (above previous limits)
        for i in range(75):
            db.upsert_file(
                full_path=f"D:/files/receipt_{i}.txt",
                filename=f"receipt_{i}.txt",
                extension=".txt",
                file_size=100 + i,
                modified_at=time.time(),
                content=f"Receipt item number {i} for financial records",
                source_type="text",
            )

        # Verify unlimited search returns all 75 matches
        results = db.search("Receipt", limit=None)
        assert len(results) == 75
        db.close()

    def test_unlimited_file_size_indexing(self, tmp_path):
        """Verify files larger than the previous 25MB limit are not skipped."""
        db_path = str(tmp_path / "large_file_db.db")
        db = Database(db_path)

        # Create a file that is 30 MB in size
        large_file = tmp_path / "large_document.txt"
        target_size = 30 * 1024 * 1024  # 30 MB
        with open(large_file, "wb") as f:
            f.seek(target_size - 1)
            f.write(b"x")

        worker = IndexerWorker(db_path=db_path, file_paths=[large_file])
        worker.run()

        # Must be indexed into database, not skipped
        assert db.count_files() == 1
        record = db.get_existing(str(large_file.resolve()))
        assert record is not None
        assert record["file_size"] == target_size
        db.close()

    def test_unlimited_vector_search(self, tmp_path):
        from core.vector_index import VectorIndex
        import numpy as np

        v_index = VectorIndex(index_dir=tmp_path / "vec_idx")
        # Add 60 vectors
        rng = np.random.default_rng(42)
        for i in range(1, 61):
            vec = rng.random(384).astype(np.float32)
            vec = vec / np.linalg.norm(vec)
            v_index.add_or_update(i, vec)

        q = rng.random(384).astype(np.float32)
        q = q / np.linalg.norm(q)

        # Search with top_k=None -> must return all 60 vectors!
        hits = v_index.search(q, top_k=None)
        assert len(hits) == 60

