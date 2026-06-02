# Changelog

## Unreleased

### Added
- **Add a casing from the dashboard.** The Dictionary page now has an "Add
  casing" form, so you can teach `GitHub`/`iPhone` directly without waiting for
  a dictation to fix. Re-adding a word overrides its canonical form (doubles as
  an edit), and each entry shows its reinforcement count.

### Changed
- The dashboard window now opens **maximized** (fills the screen) instead of a
  centered 1280×820 window. The saved size is kept as the restore-down size.
- Health-check route documented correctly as `/api/healthz` (README and
  PRODUCT_OVERVIEW previously said `/healthz`).

### Fixed
- **Casing robustness pass.** Deterministic polish no longer corrupts
  internal-caps brands during sentence-capping (`iOS`→`IOS`, `mRNA`→`MRNA`,
  `macOS`, `iPhone15` are preserved); acronym comma-lists (`SQL, iOS, GDPR`)
  keep their commas instead of being flattened by the comma-storm heuristic;
  the storm pass no longer splits internal-caps words (`TikTok`→`tikTok`);
  abbreviations (`U.S.`, `e.g.`) are not treated as sentence ends; curly/smart
  apostrophes (’ ‘) are handled like ASCII for possessives; sentences capitalize
  correctly through opening brackets/quotes, a leading apostrophe (`'twas`), and
  a unicode ellipsis (`…`); non-Latin scripts (`Étienne`, Cyrillic) are
  capitalized/flattened Unicode-aware; and honorifics (`Dr.`/`Mr.`/`Ms.`)
  survive the flattener while names after a title are capitalized.
- `add_casing` (dashboard) now strips a trailing possessive so `London's`
  teaches `London`, enforces an 80-char server-side cap, accepts digit-bearing
  tokens (`iOS17`), and only bumps the reinforcement count on a same-form
  re-add (a corrective edit is no longer counted as reinforcement). The
  `_flatten` possessive path also matches an ALLCAPS `'S` suffix, matching the
  canon path.
- **Possessives keep their casing.** `London's`/`Sam's` are no longer flattened
  to lowercase — the de-Title-Case pass now strips a trailing `'s`/`'` before
  the protected-word lookup. Learned casings also apply through the possessive
  (`tiktok's` → `TikTok's`).

### Added
- **Casing control.** Echo now learns a word's canonical casing from a single
  Fix-dialog edit (`tiktok` → `TikTok` sticks forever) and aggressively flattens
  spurious Title-Casing where Whisper/the LLM capitalized every word. Known
  proper nouns are protected: learned casings, Dictionary terms, a bundled list
  of common brands/places/names, and `I`. View and remove learned casings on the
  Dictionary page. On first run the canon is seeded from past edits in history.
  Config under `cleanup.casing`: `flatten_titlecase`, `learn_from_edits`,
  `protect_common_nouns` (all default on).

### Fixed
- Successful cleanup output (LLM and fallback paths) is now casing/punctuation-
  normalized — previously only the skip-clean fast path was, so model
  Title-Casing could reach the paste buffer untouched. Raw-on-failure,
  `provider: none`, and user-defined transform outputs are left verbatim.

### Changed
- The test suite now collects on headless/dep-light machines: `sounddevice` and
  `pynput` are lazy-imported, and `tests/conftest.py` stubs leaf native shims
  when absent. Added a fast minimal-deps CI lane.

### Removed
- Vestigial `ruvector.db` at repo root. No `src/` module referenced it;
  the active vector store is the `embedding` BLOB column on the
  `dictations` table in `data/history.db`. Personalization via
  correction-learning is planned in a future v0.X release.

### Changed
- Local-only enforcement. Removed Groq / Anthropic / OpenAI cleanup paths,
  removed `src/transcribe_cloud.py`, removed Groq HTTPS pre-warm, removed
  auto-phasing to cloud providers. Cloud API keys in the environment are
  now logged and ignored.
- Polish LLM: `qwen3.5:latest` (~6.5 GB) → `qwen2.5:3b-instruct-q4_K_M`
  (~2 GB) for VRAM headroom on 8 GB cards. Eval score went up
  (50/60 → 56/60 with a tighter default system prompt).

### Added
- **Phase 14 — Action Mode** (`experimental.action_mode`, off by default).
  Semantic voice actions behind the shared `"computer"` prefix: `open_app`
  (allowlisted `action_apps` map, no shell-from-voice), `open_url`
  (http/https/mailto only), and `web_search`. Command Mode runs first and
  falls through to Action Mode on a no-match. Every attempt is logged to the
  new `voice_actions` table. `summarize_focused`, `draft_event`, `quick_note`,
  and the dashboard panel are deferred to a follow-up PR; a LoRA intent
  classifier over logged actions is noted as future work.
- **Phase 14 PR 2** — the deferred Action Mode handlers plus security
  hardening of the shipped trio. New handlers: `summarize_focused` (local
  Ollama only, never a cloud call; reads the focused pdf/txt/md/docx),
  `draft_event` (writes a local `.ics` draft — never a calendar API), and
  `quick_note` (appends to the notes store). Adds the `focused_document_path()`
  Win32 injector helper. Hardening: `_is_safe_url` now rejects userinfo
  spoofing, percent-encoded control chars, IDN homographs, and `mailto:`
  header injection; `_RE_DOMAIN` is ASCII/TLD-anchored; `open_app` validates
  target shape and the `os.startfile` fallback is restricted to alias-shaped
  tokens; action args are redacted in the log unless
  `experimental.action_log_verbose` is set.
- New Echo Flow feather logo applied across the app: `assets/icon.png` +
  `assets/icon.ico` (exe / window icon), dashboard favicon, and the dashboard
  sidebar brand mark (`/static/logo.png`).
- Expanded notification-sound catalog. `sound.list_choices()` centralizes a
  curated set of Windows Media WAVs + system aliases (30+), surfaced as the
  picker in Settings → System for the start / stop / error cues with per-entry
  availability and a Test button. Users can still type any other WAV/alias.
- Whisper decoder biasing via `initial_prompt` built from custom
  vocabulary + snippet expansions + personal vocabulary.
- Polish eval harness at `tests/eval/` (30 cases) plus ASR eval stub.

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
