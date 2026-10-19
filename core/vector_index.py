"""
core/vector_index.py

Local vector index for semantic search, persisted under
data/semantic_index/. Two interchangeable backends behind the same
API:

  - FAISS (IndexFlatIP wrapped in IndexIDMap2), used when the `faiss`
    package is installed. IndexIDMap2 lets us add/update/remove
    vectors by an arbitrary integer id directly - so we reuse each
    file's own SQLite primary key (`files.id`) as its vector id. That
    is the file_id <-> vector_id mapping the design calls for: it's an
    identity mapping, which is the simplest reliable version of it,
    and it means there's no separate mapping table that could drift
    out of sync with the vector index.

  - A small NumPy brute-force fallback (cosine similarity via a
    normalized dot product) if `faiss` isn't installed. At the scale
    LifeSearch indexes (thousands of files, 384-dim vectors), a single
    matrix multiply is fast enough that this is a perfectly reasonable
    "simplest reliable approach" rather than a toy.

Both backends support real per-id deletion (not just "mark inactive"),
so a deleted file's vector is actually gone, not just hidden - see
remove_ids().
"""

from pathlib import Path
from typing import Optional

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX_DIR = PROJECT_ROOT / "data" / "semantic_index"

EMBEDDING_DIM = 384  # must match core.embeddings.EMBEDDING_DIM


class VectorIndex:
    """Local semantic vector store, keyed by file_id."""

    def __init__(self, index_dir: str | Path = DEFAULT_INDEX_DIR, dim: int = EMBEDDING_DIM) -> None:
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.dim = dim

        self._faiss_path = self.index_dir / "index.faiss"
        self._numpy_path = self.index_dir / "vectors.npz"

        self.backend = "faiss" if faiss is not None else "numpy"

        self._index = None                  # faiss backend
        self._ids: np.ndarray = np.array([], dtype=np.int64)      # numpy backend
        self._vectors: np.ndarray = np.zeros((0, dim), dtype=np.float32)  # numpy backend

        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self.backend == "faiss":
            if self._faiss_path.exists():
                try:
                    self._index = faiss.read_index(str(self._faiss_path))
                    return
                except Exception:
                    pass  # fall through to a fresh index below
            self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(self.dim))
        else:
            if self._numpy_path.exists():
                try:
                    data = np.load(self._numpy_path)
                    self._ids = data["ids"]
                    self._vectors = data["vectors"]
                    return
                except Exception:
                    pass  # fall through to empty arrays already set above

    def save(self) -> None:
        """Persist the index to disk. Called after each indexing run
        (not after every single file) to avoid excessive disk I/O."""
        try:
            if self.backend == "faiss":
                faiss.write_index(self._index, str(self._faiss_path))
            else:
                np.savez(self._numpy_path, ids=self._ids, vectors=self._vectors)
        except Exception as exc:
            # A failed save must never corrupt SQLite/FTS5 - just log
            # it. Worst case, this run's embeddings need regenerating
            # next launch (they're still cheap to detect via
            # embedding_status/content_hash in core.database).
            print(f"LifeSearch: failed to save semantic index: {exc}")

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_or_update(self, file_id: int, vector: np.ndarray) -> None:
        """Add a vector for file_id, replacing any existing one."""
        vector = np.asarray(vector, dtype=np.float32).reshape(1, -1)

        if self.backend == "faiss":
            id_arr = np.array([file_id], dtype=np.int64)
            try:
                self._index.remove_ids(id_arr)  # no-op if not present
            except Exception:
                pass
            self._index.add_with_ids(vector, id_arr)
        else:
            self.remove_ids([file_id])
            self._ids = np.append(self._ids, [file_id])
            self._vectors = (
                np.vstack([self._vectors, vector]) if self._vectors.shape[0] else vector
            )

    def remove_ids(self, file_ids: list[int]) -> None:
        """
        Remove vectors for the given file_ids, if present. Safe to
        call with ids that were never indexed (no-op for those). This
        is the deletion-safety mechanism: a file removed from SQLite
        has its vector removed here in the same indexing pass, so it
        genuinely cannot be returned by a later semantic search - it's
        not just filtered out after the fact.
        """
        if not file_ids:
            return
        id_arr = np.array(file_ids, dtype=np.int64)
        if self.backend == "faiss":
            try:
                self._index.remove_ids(id_arr)
            except Exception as exc:
                print(f"LifeSearch: failed to remove vectors {file_ids}: {exc}")
        else:
            if self._ids.size == 0:
                return
            mask = ~np.isin(self._ids, id_arr)
            self._ids = self._ids[mask]
            self._vectors = self._vectors[mask]

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(self, query_vector: Optional[np.ndarray], top_k: Optional[int] = None) -> list[tuple[int, float]]:
        """Return (file_id, similarity_score) pairs, best first.
        If top_k is None or <= 0, returns all matches without a limit.
        Empty list if the index is empty or query_vector is None."""
        if query_vector is None or self.count() == 0:
            return []

        q = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)

        if self.backend == "faiss":
            total = self._index.ntotal
            k = min(top_k, total) if (top_k is not None and top_k > 0) else total
            scores, ids = self._index.search(q, k)
            return [
                (int(file_id), float(score))
                for file_id, score in zip(ids[0], scores[0])
                if file_id != -1  # FAISS pads short result sets with -1
            ]
        else:
            similarities = self._vectors @ q[0]  # normalized vectors -> cosine similarity
            total = similarities.shape[0]
            k = min(top_k, total) if (top_k is not None and top_k > 0) else total
            top_indices = np.argsort(-similarities)[:k]
            return [(int(self._ids[i]), float(similarities[i])) for i in top_indices]

    def count(self) -> int:
        if self.backend == "faiss":
            return int(self._index.ntotal) if self._index is not None else 0
        return int(self._ids.shape[0])
