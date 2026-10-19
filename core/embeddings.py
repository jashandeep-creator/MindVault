"""
core/embeddings.py

Local semantic embeddings via sentence-transformers
(all-MiniLM-L6-v2), used to power semantic search alongside the
existing SQLite FTS5 exact search.

Everything here runs on-device - no network calls at query time. The
model itself is downloaded once from Hugging Face on first use (a
one-time step handled internally by sentence-transformers) and cached
locally by that library afterwards; there is no per-query network
dependency and no data ever leaves the machine.

Written to fail soft everywhere: if sentence-transformers isn't
installed, the model can't be downloaded/loaded, or embedding a
specific piece of text raises, callers get None/False back rather than
an exception. core/indexer.py and ui/main_window.py rely on this to
keep FTS5 exact search fully functional even when semantic search
can't be used.
"""

from typing import Optional

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2's fixed output size

# Module-level cache: the model is ~80MB and slow to load, so load it
# at most once per process regardless of how many times it's needed.
_model = None
_load_attempted = False
_load_error: Optional[str] = None


def _get_model():
    """Lazily load and cache the sentence-transformers model. Returns
    None (and records the reason in _load_error) if it can't be
    loaded, without raising."""
    global _model, _load_attempted, _load_error

    if _load_attempted:
        return _model

    _load_attempted = True
    if SentenceTransformer is None:
        _load_error = "sentence-transformers not installed."
        return None

    try:
        _model = SentenceTransformer(MODEL_NAME)
    except Exception as exc:
        _load_error = f"Could not load semantic search model: {exc}"
        _model = None

    return _model


def is_semantic_available() -> bool:
    """True if the embedding model is loaded and ready to use. Safe to
    call anytime - triggers the (cached) load attempt on first call."""
    return _get_model() is not None


def get_unavailable_reason() -> str:
    """Human-readable explanation for why semantic search isn't
    available, for display in the UI. Empty string if it is."""
    if is_semantic_available():
        return ""
    return _load_error or (
        "Semantic search dependencies not installed. Run: "
        "pip install -r requirements.txt"
    )


def embed_text(text: str) -> Optional[np.ndarray]:
    """
    Embed a single piece of text (e.g. a search query) into a
    normalized float32 vector. Returns None if semantic search isn't
    available or the text is blank.
    """
    model = _get_model()
    if model is None or not text or not text.strip():
        return None
    try:
        vector = model.encode(text, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32)
    except Exception:
        return None


def embed_texts(texts: list[str], batch_size: int = 32) -> Optional[np.ndarray]:
    """
    Embed a batch of texts at once (much faster per-item than calling
    embed_text in a loop for bulk indexing). Returns an (N, 384)
    float32 array of normalized vectors, or None if unavailable.
    Empty strings in the input still get a row (a zero vector) so the
    output stays aligned with the input list by index.
    """
    model = _get_model()
    if model is None:
        return None
    try:
        safe_texts = [t if t and t.strip() else " " for t in texts]
        vectors = model.encode(
            safe_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
    except Exception:
        return None
