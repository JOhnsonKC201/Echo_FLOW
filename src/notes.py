"""Notes — pinned dictations promoted to long-lived knowledge objects.

A Note is the user saying "this dictation matters, I want to find it again."
It gets a name, an optional description, and becomes a primary node in the
knowledge graph. Backlinks surface other dictations that are semantically
similar or that mention the note's title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Note:
    id: int
    dictation_id: int | None
    title: str
    description: str | None
    created_at: float
    updated_at: float


def _auto_title(cleaned_text: str, max_words: int = 8, max_chars: int = 60) -> str:
    if not cleaned_text:
        return "Untitled"
    # First sentence, or first N words.
    first = cleaned_text.split(".")[0].strip()
    words = first.split()
    title = " ".join(words[:max_words])
    if len(title) > max_chars:
        title = title[:max_chars].rstrip() + "…"
    return title or "Untitled"


def promote_to_note(history, dictation_id: int, *,
                     title: str | None = None,
                     description: str | None = None) -> int:
    """Promote a dictation to a Note. Returns the new note_id."""
    if title is None:
        row = history.conn.execute(
            "SELECT cleaned_text FROM dictations WHERE id = ?",
            (dictation_id,),
        ).fetchone()
        text = (row[0] if row else "") or ""
        title = _auto_title(text)
    return history.add_note(
        dictation_id=dictation_id, title=title, description=description,
    )


def list_notes(history, limit: int = 200) -> list[Note]:
    rows = history.list_notes(limit=limit)
    return [Note(*r) for r in rows]


def get_note(history, note_id: int) -> Note | None:
    row = history.get_note(note_id)
    return Note(*row) if row else None


def update_note(history, note_id: int, *,
                 title: str | None = None,
                 description: str | None = None) -> None:
    history.update_note(note_id, title=title, description=description)


def backlinks_for(history, retriever, note_id: int,
                   *, min_similarity: float = 0.55, limit: int = 20) -> list[tuple]:
    """Return dictations that link back to this note.

    Two link types:
      A. Semantic: cosine-similar to the note's source dictation.
      B. Lexical: the note's title appears (case-insensitive, word-boundary)
         in the dictation's cleaned text.

    Returns [(dictation_id, cleaned_text, similarity, kind), ...] sorted
    by similarity desc.
    """
    note = get_note(history, note_id)
    if note is None:
        return []
    out: dict[int, tuple] = {}

    # A. Semantic via retriever
    if retriever is not None and note.dictation_id is not None:
        try:
            src = history.conn.execute(
                "SELECT raw_text FROM dictations WHERE id = ?",
                (note.dictation_id,),
            ).fetchone()
            if src and src[0]:
                # Use the retriever's real primary keys — recovering the row by
                # re-querying on raw_text is wrong (raw_text isn't unique and
                # collides across repeated utterances, linking the wrong row).
                results = retriever.search_with_ids(src[0])
                for rid, raw, cleaned, sim in results:
                    if sim < min_similarity:
                        continue
                    if rid == note.dictation_id:
                        continue
                    out[int(rid)] = (int(rid), cleaned, sim, "semantic")
        except Exception as e:
            import logging
            logging.getLogger("wispr.notes").warning("semantic backlinks failed: %s", e)

    # B. Lexical — title appears in cleaned_text
    title = (note.title or "").strip()
    if title and len(title) >= 4:
        try:
            pattern = re.escape(title)
            rows = history.conn.execute(
                "SELECT id, cleaned_text FROM dictations "
                "WHERE cleaned_text LIKE ? AND id != ? "
                "ORDER BY ts DESC LIMIT ?",
                (f"%{title}%", note.dictation_id or -1, limit * 2),
            ).fetchall()
            for did, cleaned in rows:
                # Word-boundary check to avoid substring noise
                if re.search(r"\b" + pattern + r"\b", cleaned or "", re.IGNORECASE):
                    did = int(did)
                    if did not in out:
                        out[did] = (did, cleaned, 1.0, "lexical")
        except Exception as e:
            import logging
            logging.getLogger("wispr.notes").warning("lexical backlinks failed: %s", e)

    backlinks = list(out.values())
    backlinks.sort(key=lambda r: r[2], reverse=True)
    return backlinks[:limit]
