"""Tag suggestion + application for dictations.

Three signals feed the suggester:

  Signal 1 (cluster):   Top TF-IDF terms from the dictation's k-means cluster.
                        Surface them as candidate tags. Confidence 0.7.

  Signal 2 (similar):   Cosine-match against past dictations. If the top-K
                        neighbors share confirmed tags, propose those.
                        Confidence proportional to similarity.

  Signal 3 (concept):   CamelCase / acronym / proper-noun extraction from the
                        cleaned text. If any matches a known tag, propose it.
                        Confidence 0.6.

Suggestions get written to `dictation_tags` with `confirmed=0`. The editor UI
surfaces them as ghost chips the user accepts or rejects. Manual tagging
upgrades them to `confirmed=1`.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .graph import _extract_concepts


MAX_SUGGESTIONS = 5
MIN_CONFIDENCE = 0.50
SIMILAR_K = 8
SIMILAR_THRESHOLD = 0.65


@dataclass
class TagSuggestion:
    name: str
    confidence: float
    source: str   # 'cluster' | 'similar' | 'concept'


def _normalize_tag_name(s: str) -> str:
    """Lowercase, strip, replace whitespace with hyphens, drop punctuation."""
    import re
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\-_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _cluster_signal(cluster_label: str | None) -> list[TagSuggestion]:
    """Cluster labels look like 'Shift · Ctrl' — split on '·' and emit each term."""
    if not cluster_label:
        return []
    out = []
    for piece in cluster_label.split("·"):
        name = _normalize_tag_name(piece)
        if name and len(name) >= 3:
            out.append(TagSuggestion(name=name, confidence=0.70, source="cluster"))
    return out


def _similar_signal(retriever, cleaned_text: str, history) -> list[TagSuggestion]:
    """Cosine-match against past dictations and inherit their confirmed tags."""
    if retriever is None or history is None:
        return []
    try:
        # Retriever.search returns [(raw, cleaned, sim), ...] without ids,
        # so we re-query the DB by raw_text to recover the dictation ids.
        results = retriever.search(cleaned_text)
    except Exception:
        return []
    if not results:
        return []
    # Aggregate tag votes weighted by similarity.
    votes: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    for raw, _cleaned, sim in results[:SIMILAR_K]:
        if sim < SIMILAR_THRESHOLD:
            continue
        try:
            row = history.conn.execute(
                "SELECT id FROM dictations WHERE raw_text = ? ORDER BY ts DESC LIMIT 1",
                (raw,),
            ).fetchone()
        except Exception:
            continue
        if not row:
            continue
        for name, _src, _conf, confirmed in history.get_tags_for_dictation(
            int(row[0]), confirmed_only=True
        ):
            votes[name] += float(sim)
            counts[name] += 1
    suggestions = []
    for name, weight in votes.items():
        # Average similarity across the neighbors that voted for this tag.
        avg_sim = weight / counts[name]
        suggestions.append(TagSuggestion(
            name=name, confidence=round(avg_sim * 0.85, 3), source="similar"
        ))
    return suggestions


def _concept_signal(cleaned_text: str, known_tags: set[str]) -> list[TagSuggestion]:
    """Extract concepts and propose any that match a known tag."""
    if not known_tags:
        return []
    concepts = _extract_concepts(cleaned_text)
    out = []
    seen = set()
    for c in concepts:
        name = _normalize_tag_name(c)
        if name in known_tags and name not in seen:
            seen.add(name)
            out.append(TagSuggestion(name=name, confidence=0.60, source="concept"))
    return out


def _merge_suggestions(*lists: list[TagSuggestion]) -> list[TagSuggestion]:
    """Collapse duplicates across signals, keeping the max confidence per tag."""
    by_name: dict[str, TagSuggestion] = {}
    for lst in lists:
        for s in lst:
            existing = by_name.get(s.name)
            if existing is None or s.confidence > existing.confidence:
                by_name[s.name] = s
    return list(by_name.values())


def suggest_tags(
    cleaned_text: str,
    *,
    cluster_label: str | None = None,
    retriever=None,
    history=None,
    max_suggestions: int = MAX_SUGGESTIONS,
) -> list[TagSuggestion]:
    """Return ranked tag suggestions from the three signals.

    `cluster_label` is optional — pass it if you already know which cluster
    this dictation lives in (e.g. from build_dictation_graph). If None, the
    cluster signal is silently skipped.
    """
    cluster_sigs = _cluster_signal(cluster_label)
    sim_sigs = _similar_signal(retriever, cleaned_text, history)
    known = history.known_tag_names(confirmed_only=True) if history is not None else set()
    concept_sigs = _concept_signal(cleaned_text, known)
    merged = _merge_suggestions(cluster_sigs, sim_sigs, concept_sigs)
    merged.sort(key=lambda s: s.confidence, reverse=True)
    return [s for s in merged if s.confidence >= MIN_CONFIDENCE][:max_suggestions]


def apply_suggestions(history, dictation_id: int,
                       suggestions: list[TagSuggestion]) -> int:
    """Persist suggestions to dictation_tags with confirmed=0. Returns count."""
    n = 0
    for s in suggestions:
        try:
            history.set_tag(
                dictation_id, s.name,
                source=s.source, confidence=s.confidence, confirmed=False,
            )
            n += 1
        except Exception:
            continue
    return n
