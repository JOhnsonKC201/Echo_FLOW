# Changelog

## 0.1.0 — 2026-05-20

First numbered version. The day a lot happened.

### Added
- Self-grading layer (`src/grade.py`): every dictation gets a 0–100 quality score from four signals (Whisper confidence, hallucination guard, semantic coherence, pattern coverage). Stored alongside each row.
- Self-improving loops: online weight calibration via SGD against user-edited dictations + exponential pattern decay (14-day half-life) so old jargon fades.
- LLM-free `learned` cleanup provider: uses past corrections + learned token substitutions + deterministic polish. Falls back to Ollama when not confident.
- Four-phase auto-progression: Bootstrap (Groq) → Hybrid (local Whisper + Groq cleanup) → Independent (local + Ollama) → Self-Sufficient (no LLM).
- Re-paste hotkey (default Ctrl+Shift+Win): re-pastes the most recent dictation in the focused window. Fires on release to avoid modifier-key interference. Cached in RAM to beat the async DB write race.
- Snippet expansion: short codes (btw, fyi, lgtm, ttyl, ...) expand post-cleanup. Case-aware, word-boundary safe.
- A/B provider shadow testing: runs primary + alternate cleanup providers, grades both, logs the winner. Opt-in via `cleanup.ab_test.enabled`.
- Knowledge graph: D3.js force-directed visualization with Notes mode (default when notes exist), Dictations mode, Concepts mode. Tag filter chip cloud, search box, quality slider with green/amber/red rings, refresh button, time slider.
- Notes layer: pinning promotes a dictation to a long-lived knowledge object with title and description.
- Tags: three-signal auto-suggestion (cluster, similar, concept) with manual confirm. Persists to `dictation_tags`.
- Action items: regex-based extraction of TODO-style phrases. Blocklist for daily drivel (`go to bed`, `eat lunch`). Silent — only surfaces in the editor.
- Review queue: tray menu opens a worst-quality-first list of un-edited dictations.
- Pin last dictation: tray menu shortcut to promote the most recent dictation to a Note.
- Editor extensions: tag chip row with accept/reject, manual tag entry, pin button, action items checklist.

### Changed
- Embedding model: `paraphrase-multilingual-MiniLM-L12-v2` (118 MB) → `all-MiniLM-L6-v2` (22 MB, ~3x faster). Existing embeddings auto-rebackfilled on startup via `embedding_model` column check.
- Language scope: English only. Removed Spanish and Nepali style prompts, language override menu, and multilingual filler-word lists.
- Logging: critical startup events (Phase banner, Whisper backend choice, Ready event, Re-paste hotkey, per-dictation raw/cleaned) now reach `data/wispr.log` even when running silent via VBS.

### Fixed
- Race condition where Ctrl+Shift+Win pasted the previous dictation instead of the most recent one (async DB write hadn't committed). RAM cache now sourcing.
- Synthetic Ctrl+V from re-paste landed mangled by user's still-held physical modifiers. Re-paste now fires on key release with a 60 ms safety delay.
- Dictation hotkey vetoes when Win is added mid-press — recording silently aborts instead of leaving stale audio.
- TF-IDF cluster labels duplicated word stems (`Thank · Thank Thank`). Unigrams only now, with dedupe.

### Infrastructure
- 81 tests (up from 11 at the start of the day). Tests for actions, tags, notes, grading, snippet expansion, A/B logging, veto behavior, re-paste cache.
- Schema migrations are idempotent and additive. Five tables added without losing existing data.
- Folder reorg: dev/maintenance scripts moved to `scripts/`, `ruvector.db` moved to `data/`. Root has only the 5 user-clickable entry points.
- Distribution script (`scripts/prepare_for_distribution.bat`) produces a 0.21 MB clean copy with no personal data, no caches, no venv.
- Silent exception swallowers (`except Exception: pass`) in critical paths replaced with `_log.warning` calls for post-mortem visibility.
