"""Semantic search over dictation history — the user-facing counterpart to RAG.

Echo Flow already embeds every dictation into a 384-dim L2-normalized vector
(`src/retrieval.py`) and stores it as a float32 BLOB in `dictations.embedding`.
RAG's `Retriever.search()` uses those vectors for *few-shot* retrieval — it's
deliberately narrow (corrections only, `k=6`, `min_similarity=0.35`). This module
reuses the same primitives (`retrieval.embed`, `retrieval.from_blob`, the shared
model singleton) but answers a different question: "show me everything I've ever
dictated that *means* this", across all rows, displaying the cleaned text.

Scale note: a brute-force cosine scan over the whole table is correct under
~10k dictations (10k x 384 x float32 ~= 15 MB; a single numpy matmul is
sub-millisecond). Past that, swap `_scan`'s linear pass for an ANN index
(faiss / hnswlib) keyed on the same BLOBs — the storage format is unchanged.

Pure functions: every entry point takes a sqlite3 connection. No Flask here.
"""
from __future__ import annotations

import sqlite3

import numpy as np

from .. import retrieval
from . import inbox as _inbox


def _scan(
    conn: sqlite3.Connection,
    qv: np.ndarray,
    *,
    limit: int,
    min_sim: float,
    trust_mobile: bool,
    exclude_id: int | None = None,
) -> list[dict]:
    """Cosine-rank every embedded dictation against query vector `qv`.

    `qv` and the stored vectors are both L2-normalized, so a dot product *is*
    cosine similarity. Returns ranked dicts above `min_sim`, capped at `limit`.
    """
    # Defense-in-depth: exclude untrusted mobile-sourced rows by default, matching
    # RAG's posture (the mobile bridge shouldn't surface in desktop search either).
    source_filter = "" if trust_mobile else " AND source != 'mobile'"
    try:
        rows = conn.execute(
            "SELECT id, ts, window_title, style, cleaned_text, embedding "
            "FROM dictations WHERE embedding IS NOT NULL AND cleaned_text != ''"
            + source_filter
        ).fetchall()
    except Exception:
        return []
    if not rows:
        return []

    ids: list[int] = []
    metas: list[tuple] = []
    vecs: list[np.ndarray] = []
    for rid, ts, win, style, cleaned, blob in rows:
        rid = int(rid)
        if exclude_id is not None and rid == exclude_id:
            continue
        try:
            v = retrieval.from_blob(blob)
        except Exception:
            continue
        if v.shape[0] != qv.shape[0]:  # stale/short blob — skip rather than crash
            continue
        ids.append(rid)
        metas.append((ts, win, style, cleaned))
        vecs.append(v)
    if not vecs:
        return []

    mat = np.vstack(vecs)            # (N, 384)
    sims = mat @ qv                  # (N,) — cosine, both sides normalized
    order = np.argsort(-sims)        # descending

    out: list[dict] = []
    for i in order:
        s = float(sims[i])
        if s < min_sim:
            break                    # sorted desc → everything after is below too
        ts, win, style, cleaned = metas[i]
        out.append({
            "id": ids[i],
            "ts": ts,
            "ts_human": _inbox.format_ts(ts or 0.0),
            "window_title": win or "",
            "style": style or "default",
            "text": cleaned or "",
            "similarity": round(s, 4),
        })
        if len(out) >= limit:
            break
    return out


def search_text(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 25,
    min_sim: float = 0.25,
    trust_mobile: bool = False,
) -> list[dict]:
    """Embed a free-text query and return the most semantically similar dictations."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        qv = retrieval.embed(q)
    except Exception:
        return []
    return _scan(conn, qv, limit=limit, min_sim=min_sim, trust_mobile=trust_mobile)


def similar_to_id(
    conn: sqlite3.Connection,
    dictation_id: int,
    *,
    limit: int = 8,
    min_sim: float = -1.0,
    trust_mobile: bool = False,
) -> list[dict]:
    """Nearest-neighbors of an existing dictation — the "Find similar" affordance.

    Uses the row's *stored* embedding (no re-embedding) and excludes the row
    itself. `min_sim=-1.0` (cosine's floor) so it always surfaces the closest
    matches up to `limit`, even weakly- or negatively-correlated rows — like the
    Embedding Projector's "nearest points" panel. The per-row `similarity` field
    lets the UI decide what to dim or hide. (A `0.0` floor silently dropped
    negatively-correlated neighbors, contradicting "always surfaces".)
    """
    try:
        row = conn.execute(
            "SELECT embedding FROM dictations WHERE id = ?", (int(dictation_id),)
        ).fetchone()
    except Exception:
        return []
    if row is None or row[0] is None:
        return []
    try:
        qv = retrieval.from_blob(row[0])
    except Exception:
        return []
    return _scan(
        conn, qv, limit=limit, min_sim=min_sim,
        trust_mobile=trust_mobile, exclude_id=int(dictation_id),
    )
