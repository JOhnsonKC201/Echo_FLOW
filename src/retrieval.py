"""RAG retrieval: find the K most semantically similar past dictations.

Instead of feeding the LLM your *last 6* dictations as few-shot examples
(chronological recency), we embed every past dictation into a 384-dim vector
and retrieve the *6 most similar* to the current one (semantic relevance).

When you make the same grammar mistake you made last week, that specific
correction gets retrieved and reinforced.

Tech stack:
- sentence-transformers `all-MiniLM-L6-v2` (22MB, English-only, fast on CPU)
- Embeddings stored as float32 BLOB in SQLite (no separate vector DB needed
  until you hit ~100k dictations)
- Cosine similarity via numpy (plenty fast for <10k vectors)
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass

import numpy as np

from . import log as wlog
_log = wlog.get("retrieval")


# Module-level singleton — loading the model is expensive (~3s), do it once
_model = None
_model_lock = threading.Lock()
_model_name = "sentence-transformers/all-MiniLM-L6-v2"


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(_model_name)
    return _model


def embed(text: str) -> np.ndarray:
    """Return a 384-dim L2-normalized float32 vector."""
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return vec.astype(np.float32)


def to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


@dataclass
class RetrievalConfig:
    enabled: bool = True
    k: int = 6
    min_similarity: float = 0.35   # below this, treat as "no good match"
    backfill_on_startup: bool = True   # embed any rows that lack vectors
    # When False (default), rows logged with source='mobile' are excluded from
    # RAG retrieval so the untrusted mobile bridge can't poison the desktop
    # few-shot pool. Flip to True only if you trust everything posted via /v1/dictate.
    trust_mobile: bool = False


class Retriever:
    def __init__(self, db_path: str, cfg: RetrievalConfig):
        self.db_path = db_path
        self.cfg = cfg
        self._ready = False
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def warm(self) -> None:
        """Pre-load the model + backfill embeddings for old rows. Run in a thread."""
        if not self.cfg.enabled or self._ready:
            return
        try:
            _get_model()
            if self.cfg.backfill_on_startup:
                self._backfill()
            self._ready = True
        except Exception as e:
            _log.error("warm failed: %s", e)

    def _backfill(self) -> None:
        """Embed any rows missing an embedding OR tagged with a stale model."""
        try:
            with closing(self._conn()) as conn:
                rows = conn.execute(
                    "SELECT id, raw_text FROM dictations "
                    "WHERE raw_text != '' AND "
                    "(embedding IS NULL OR embedding_model IS NULL OR embedding_model != ?)",
                    (_model_name,),
                ).fetchall()
                if not rows:
                    return
                _log.info(
                    "backfilling embeddings for %d rows (model=%s)",
                    len(rows), _model_name,
                )
                t0 = time.time()
                for row_id, raw in rows:
                    try:
                        vec = embed(raw)
                        conn.execute(
                            "UPDATE dictations SET embedding = ?, embedding_model = ? WHERE id = ?",
                            (to_blob(vec), _model_name, row_id),
                        )
                    except Exception:
                        continue
                conn.commit()
                _log.info("backfill done in %.1fs", time.time() - t0)
        except Exception:
            return

    @staticmethod
    def model_name() -> str:
        return _model_name

    def embed_text(self, text: str) -> np.ndarray | None:
        if not self.cfg.enabled or not text.strip():
            return None
        try:
            return embed(text)
        except Exception as e:
            _log.error("embed failed: %s", e)
            return None

    def search(self, query_text: str, style: str | None = None) -> list[tuple[str, str, float]]:
        """Return [(raw, cleaned, similarity), …] sorted desc."""
        if not self.cfg.enabled:
            return []
        qv = self.embed_text(query_text)
        if qv is None:
            return []
        # Defense-in-depth: filter mobile-sourced rows out of RAG by default.
        # Schema guarantees source defaults to 'desktop' for legacy rows.
        source_filter = "" if self.cfg.trust_mobile else " AND source != 'mobile'"
        try:
            with closing(self._conn()) as conn:
                if style:
                    rows = conn.execute(
                        "SELECT raw_text, cleaned_text, embedding FROM dictations "
                        "WHERE embedding IS NOT NULL AND style = ? AND raw_text != cleaned_text"
                        + source_filter,
                        (style,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT raw_text, cleaned_text, embedding FROM dictations "
                        "WHERE embedding IS NOT NULL AND raw_text != cleaned_text"
                        + source_filter
                    ).fetchall()
        except Exception:
            return []
        if not rows:
            return []
        scored: list[tuple[str, str, float]] = []
        for raw, cleaned, blob in rows:
            try:
                v = from_blob(blob)
                sim = float(np.dot(qv, v))   # both are L2-normalized → cosine
                if sim >= self.cfg.min_similarity:
                    scored.append((raw, cleaned, sim))
            except Exception:
                continue
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[: self.cfg.k]

    def search_with_ids(
        self, query_text: str, style: str | None = None
    ) -> list[tuple[int, str, str, float]]:
        """Like search(), but returns [(id, raw, cleaned, similarity), …].

        Callers that need to look up the matched row (e.g. to inherit its tags)
        must use the actual primary key — recovering it by re-querying on
        raw_text is wrong, because raw_text is not unique and collides across
        repeated utterances, attributing the wrong row's tags.
        """
        if not self.cfg.enabled:
            return []
        qv = self.embed_text(query_text)
        if qv is None:
            return []
        source_filter = "" if self.cfg.trust_mobile else " AND source != 'mobile'"
        try:
            with closing(self._conn()) as conn:
                if style:
                    rows = conn.execute(
                        "SELECT id, raw_text, cleaned_text, embedding FROM dictations "
                        "WHERE embedding IS NOT NULL AND style = ? AND raw_text != cleaned_text"
                        + source_filter,
                        (style,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, raw_text, cleaned_text, embedding FROM dictations "
                        "WHERE embedding IS NOT NULL AND raw_text != cleaned_text"
                        + source_filter
                    ).fetchall()
        except Exception:
            return []
        if not rows:
            return []
        scored: list[tuple[int, str, str, float]] = []
        for rid, raw, cleaned, blob in rows:
            try:
                v = from_blob(blob)
                sim = float(np.dot(qv, v))   # both are L2-normalized → cosine
                if sim >= self.cfg.min_similarity:
                    scored.append((int(rid), raw, cleaned, sim))
            except Exception:
                continue
        scored.sort(key=lambda x: x[3], reverse=True)
        return scored[: self.cfg.k]
