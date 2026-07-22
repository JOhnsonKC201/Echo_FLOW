# Changelog

All notable changes are documented here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added
- **Humanize — paste AI-written text, get a human version back**
  (`Cleaner.humanize_text`, dashboard → **My Voice → Humanize**). Paste prose a
  language model wrote and get it back reading like a person. Separate from the
  dictation "My Voice" pass, which only nudges text the user already wrote — a
  genuine de-AI rewrite deletes LLM vocabulary, dropping token overlap to ~0.15,
  far under that pass's 0.35/0.85 floors, so it declined every real rewrite.
  This is its own method, prompt, and guards; the dictation path is untouched.

  **Three selectable targets** (no writing samples required to start):
  - **A natural human** — strip the AI tells (em-dash rhythm, *delve / moreover
    / a testament to*, "it's not just X, it's Y", tricolons, hedging stacks) and
    return plain natural prose. The default; needs no setup.
  - **Me** — additionally match your writing samples. With none it falls back to
    the natural-human rewrite and says so, rather than refusing.
  - **A specific tone** — casual, professional, friendly, plain, confident, or
    concise, chosen from a dropdown.

  **It always returns a result.** A risky-but-readable rewrite (a number
  changed, meaning drifted a little) is shown *with a warning* rather than
  dropped; only genuinely broken output (a preamble, injected markdown, a merged
  or ballooned paragraph, off-topic text, or an echo of your writing samples)
  falls back to your original. The page shows a word-level diff of what changed.

  The guards came out of measuring the real local model, not theory:
  - **A paragraph at a time.** Handed a whole document, `qwen2.5:3b` merges
    paragraphs; rewriting each separately preserves structure structurally and
    lets one bad paragraph fall back without sinking the rest.
  - **Numbers are checked exactly, in both directions.** The benchmark caught
    the model turning *"caught 14 regressions before release"* into *"shows how
    solid the process is"* — fluent, close, and no longer true. A changed number
    is now surfaced as a warning on the shown rewrite.
  - **Voice-profile regurgitation is rejected** (voice mode). A small model may
    reproduce the samples' *subject matter*; detected per sentence, and a purely
    leading echo is trimmed rather than discarded.
  - **Prompt ordering is load-bearing.** With the profile appended last the
    model continued from it; the profile is delimited and the rules come last.
  - **Reasoning models are handled.** A thinking model (qwen3.5, deepseek-r1)
    spends its budget on `thinking` and returns empty `content`, which reads as
    a dead provider; this path sends `think: false`.

  `Cleaner.humanize_text` returns a `HumanizeOutcome(text, reason, warnings,
  changed, total)`. `experimental` keys: `humanize_text_model` (blank = the
  cleanup model; this pass runs on a button press, so a larger local model is an
  option — editable in **Settings → Experimental**), `humanize_text_timeout_sec`,
  `humanize_text_min_sim`, `humanize_text_max_chars`.

  Then, in the same cycle:
  - **Deterministic AI-tell detector** (`src/aitells.py`) — a pure, tested module
    that scores how much a passage still reads like a model (LLM vocabulary,
    em-dash rhythm, the "not just X" antithesis, hedging, throat-clearers). The
    page shows an **"AI tells: N → M"** score on every result and lists what
    still remains, so the rewrite is legible instead of a black box.
  - **Better output on the small model.** Few-shot before→after examples in the
    prompt, plus a budget-bounded **tell-polish second pass**: when a clean
    rewrite still scores tells, one focused "remove exactly these phrases" call
    that is kept only if it clears the same guards and strictly lowers the score.
    On the benchmark this took the previously-failing dense/technical case to a
    clean rewrite, and most cases to zero remaining tells.
  - **Auto model escalation** (`humanize_text_escalate_model`, default `"auto"`).
    When the main model mangles a paragraph, it retries once on the next-step-up
    installed model — chosen by size so it won't jump to one too big for the GPU
    — before falling back to your original. Verified live: the 3B's hardest case
    is rescued by `qwen3.5`.
  - **More control.** A **strength** selector (light / balanced / aggressive)
    that steers how far to rewrite and scales the length budget, and a **custom
    free-text tone** box (sanitized) alongside the presets. Plus a **Try again**
    button to re-roll a different rewrite.
  - **`scripts/eval_humanize.py`** — a committed quality benchmark (fixture
    corpus + `--check` release gate) that measures acceptance, tells-removed (via
    `aitells`), facts-kept, and voice contamination against the real model.
  - **Inline tell highlighting + broader detection.** The detector grew from ~65
    to ~130 tells (more LLM vocabulary, "plays a crucial role", "a wide range
    of", "in conclusion", "unlock the potential", "first and foremost", …). The
    result now marks any remaining tells *in place* (`aitells.segments`), and a
    "AI tells in your paste" panel shows exactly what the pass targeted — so both
    ends are legible, not just a number.

- **Local intent model — a regex-miss fallback for Action Mode**
  (`src/intent_model.py`, opt-in, **off by default**). Action Mode classifies a
  prefixed command with tight anchored regexes; that is high-precision but
  brittle to phrasing (*"launch spotify"* instead of *"open spotify"*, *"play
  some music"* instead of *"play music"*). When
  `experimental.action_intent_model` is enabled, a miss on an explicit
  (`"computer, …"`) command is retried through a local predictor that recovers
  common verb-synonym and filler phrasings. It is built around one locked
  safety invariant: **the model never fires a side effect the regex/allowlist
  wouldn't.** The predictor proposes only a handler name + a slot string, which
  is re-validated by `build_match()` through the *same* guards as the regex path
  (`_domain_to_url` / `_is_safe_url` / the `action_apps`/`action_folders`
  allowlists) — and it is *stricter* than the regex path, refusing an
  unconfigured app/folder at construction time. Today's predictor is a
  dependency-free keyword heuristic (no ML deps, no import cost); the module is
  the load-once seam where an embedding + logistic-regression head can be
  dropped in later via `set_predictor()`.
  - A **`shadow`** value for the flag logs what the model *would* have fired
    without executing it, so precision can be measured before the model is ever
    trusted to act. A new confidence floor (`action_intent_min_conf`, default
    `0.75`) and a cheap length pre-gate keep it conservative.
  - **Offline eval harness** `scripts/eval_intent.py`: scores the predictor on a
    labeled fixture set (precision / recall / F1 + a `min_conf` sweep and
    confusion of misses), and a `--check` gate that fails CI if precision or
    recall regress — the empirical basis for the `0.75` default. Covered by
    `tests/test_intent_model.py` (safety re-validation, recovery, abstain, floor,
    never-raises) and `tests/test_main_intent_model.py` (off-by-default, live
    recovery, shadow-does-not-execute, and *unconfigured-app-can't-launch*).
  - **Dashboard control.** Settings → Experimental now surfaces the fallback as
    an Off / On / Shadow select plus a confidence-floor field, so it is reachable
    without hand-editing `config.yaml`. The tri-state maps to real YAML types
    (`false` / `true` / `"shadow"`) — "off" writes a boolean, never the truthy
    string `"false"` — and the floor is validated to `0–1`.
  - **Learned model backend** (`action_intent_backend: model`). Beyond the
    keyword rules, a tiny embedding + logistic-regression head
    (`src/intent_classifier.py`) generalizes to phrasings no rule anticipated —
    *"hush"* → mute, *"make a memo that…"* → note, *"the thing I just copied"* →
    clipboard — by embedding the utterance with the app's existing local
    sentence-transformers model (`retrieval.embed`, 384-dim, CPU) and classifying
    the intent. It emits only a handler + slot, so it flows through the same
    `build_match` guards as everything else (an unconfigured app it proposes
    still resolves to nothing). No new dependencies (numpy LR, ~13 classes), and
    it trains out-of-the-box from a shipped seed corpus (`src/intent_seed.py`) —
    a fresh install works with zero user data; the artifact is cached lazily to
    `data/intent_model.npz`. `scripts/train_intent.py` (`--train` / `--eval` /
    `--probe`) builds it, measures stratified-holdout accuracy (~0.83 on the
    seed) to tune the model floor (`action_intent_model_min_conf`, default
    `0.4` — the diffuse 13-class softmax sits lower than the keyword floor), and
    can sharpen it by mining the user's own `voice_actions` history. Covered by
    `tests/test_intent_classifier.py` (LR train/serialize, slot extraction,
    predict/abstain/never-raise, backend selection, and the same
    unconfigured-app-can't-launch safety proof, all with a fast fake embedder).
- **Automated signed-release pipeline.** A new `release` GitHub Actions workflow
  (`.github/workflows/release.yml`) builds the daemon installer on a tagged
  push (`v*`): PyInstaller → Inno Setup → SHA256 → draft GitHub Release with the
  installer + checksum attached. Code signing is an **opt-in step** that
  activates automatically when a `CODESIGN_PFX_BASE64` secret is present — ship
  unsigned today, drop a cert in later with zero workflow changes. A
  `workflow_dispatch` path does a dry-run build without creating a release. The
  job also fails fast if the tag doesn't match `src/__init__.py` `__version__`.
  Runbook in [`installer/RELEASING.md`](installer/RELEASING.md).
- **winget manifest.** `packaging/winget/` ships a schema-1.6.0 manifest
  (`JOhnsonKC201.EchoFlow`) so the app can be installed with
  `winget install JOhnsonKC201.EchoFlow` once published. Per-release update and
  submission steps in [`packaging/winget/README.md`](packaging/winget/README.md).
- **Lightweight web installer** (`installer/EchoFlow-Web-Setup.iss`). A tiny
  per-user bootstrapper that downloads the daemon payload from the GitHub
  release at install time (SHA256-verified, progress bar) and extracts it,
  instead of bundling hundreds of MB. Shares its AppId and install location
  with the full installer, so both resolve to one installed product. The
  release workflow now publishes three assets: the full offline installer, the
  web installer, and the `EchoFlow-Daemon-Payload-<ver>.zip` it fetches.
- **Opt-in self-update check** (`update.check_on_startup`, default **off**).
  When enabled, the daemon makes a single anonymous GitHub Releases API call at
  launch and shows a tray toast if a newer version exists — no history, config,
  or identifiers are ever sent (`src/update_check.py`). The /privacy ledger is
  updated to report this honestly: with the check on, it no longer claims zero
  egress and names the endpoint. Fully covered by `tests/test_update_check.py`.

### Changed
- The My Voice page's "Try it" box is now the **Humanize** workspace. It
  previously previewed the light-touch dictation pass, which is not what the
  page is used for; the shadow-preview table already answers "should I trust
  this for dictation?" with real data. `POST /myvoice/preview` is removed along
  with it rather than left as an unreachable endpoint.
- Installer version is now single-sourced. Both `.iss` scripts honor an
  `iscc /DMyAppVersion=<ver>` override (CI passes the tag); the hardcoded
  `#define` is now just a local-build fallback. Fixed the stale repo URL in
  `installer/EchoFlow.iss`.

## 0.2.0 — 2026-06-17

The dashboard era: a full local web dashboard, the casing-control system,
experimental voice Action Mode, opt-in cloud cleanup, and a hardening pass
across the daemon lifecycle.

### Added
- **Casing control.** Echo now learns a word's canonical casing from a single
  Fix-dialog edit (`tiktok` → `TikTok` sticks forever) and aggressively flattens
  spurious Title-Casing where Whisper/the LLM capitalized every word. Known
  proper nouns are protected: learned casings, Dictionary terms, a bundled list
  of common brands/places/names, and `I`. View and remove learned casings on the
  Dictionary page. On first run the canon is seeded from past edits in history.
  Config under `cleanup.casing`: `flatten_titlecase`, `learn_from_edits`,
  `protect_common_nouns` (all default on).
- **Add a casing from the dashboard.** The Dictionary page now has an "Add
  casing" form, so you can teach `GitHub`/`iPhone` directly without waiting for
  a dictation to fix. Re-adding a word overrides its canonical form (doubles as
  an edit), and each entry shows its reinforcement count.
- **Cloud cleanup opt-in (`cleanup.allow_cloud_cleanup`).** You can now route
  *every* dictation's cleanup through a cloud provider (Groq / Anthropic) instead
  of local Ollama, trading the local-only guarantee for cleanup quality. Off by
  default; when on, a missing API key or a failed cloud call falls back to local
  Ollama so dictation never breaks. PE mode and the teacher loop already used the
  cloud — this extends it to regular cleanup. (Set `cleanup.provider: groq` +
  `cleanup.allow_cloud_cleanup: true`, and export `GROQ_API_KEY`.)
- **Phase 14 — Action Mode** (`experimental.action_mode`, off by default).
  Semantic voice actions behind the shared `"computer"` prefix: `open_app`
  (allowlisted `action_apps` map, no shell-from-voice), `open_url`
  (http/https/mailto only), and `web_search`. Command Mode runs first and
  falls through to Action Mode on a no-match. Every attempt is logged to the
  new `voice_actions` table.
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

### Changed
- The dashboard window now opens **maximized** (fills the screen) instead of a
  centered 1280×820 window. The saved size is kept as the restore-down size.
- Health-check route documented correctly as `/api/healthz` (README and
  PRODUCT_OVERVIEW previously said `/healthz`).
- **`reload_config` is now atomic.** All config-derived values
  (vocabulary/initial_prompt, PE block, learner trust flags, cleaner config) are
  computed before any are applied, so a failure mid-reload leaves the previous
  config fully intact instead of half-applied.
- **Local-only enforcement.** Removed Groq / Anthropic / OpenAI cleanup paths
  from the default path, removed `src/transcribe_cloud.py`, removed the Groq
  HTTPS pre-warm, and removed auto-phasing to cloud providers. Cloud API keys in
  the environment are logged and ignored unless an opt-in cloud feature is
  explicitly enabled.
- Polish LLM: `qwen3.5:latest` (~6.5 GB) → `qwen2.5:3b-instruct-q4_K_M`
  (~2 GB) for VRAM headroom on 8 GB cards. Eval score went up
  (50/60 → 56/60 with a tighter default system prompt).
- The test suite now collects on headless/dep-light machines: `sounddevice` and
  `pynput` are lazy-imported, and `tests/conftest.py` stubs leaf native shims
  when absent. Added a fast minimal-deps CI lane.
- **Dev/test dependencies split into `requirements-dev.txt`** (`pytest`,
  `pytest-mock`, `pytest-cov`, `pytest-timeout`); the runtime `requirements.txt`
  no longer carries test tooling. `scripts\setup.bat` and `scripts\run_tests.bat`
  now operate from the repo root (they previously created/activated a venv inside
  `scripts\`).

### Fixed
- **Casing now survives every fallback path (full-system audit, 2026-06-03).**
  Whisper's "Every Word Capitalized" output previously reached the user
  unflattened whenever cleanup took a raw-passthrough exit: the hallucination
  guard (model went off-track), total provider failure (all providers down),
  and the `learned` provider with `fallback_to_ollama: false`. All three now
  run the LLM-free, content-preserving casing/punctuation pass — your words are
  kept verbatim, only the casing is normalized. (Root cause of the "sometimes
  capitalized, sometimes correct" reports; note a running daemon must be
  restarted to pick up the fix.)
- **Settings pages reflect the live theme.** The five `/settings/*` panels
  captured the theme once at startup, so a light/dark toggle made elsewhere
  wasn't shown on those pages until restart. They now read the current theme on
  every render like the rest of the dashboard.
- **A failing hotkey callback no longer silently kills dictation.** If
  `recorder.start()` raised (e.g. the mic was unplugged mid-session), the
  exception escaped into pynput's listener thread and stopped *all* hotkey
  detection with no indicator. Activate/deactivate callbacks are now guarded
  and logged, so the listener survives.
- **Semantic backlinks link the right dictation.** `notes.backlinks_for` used
  the retriever's match then re-looked-up the row by `raw_text` (not unique —
  repeated utterances collide), occasionally attributing the wrong dictation.
  It now uses the matched row's real primary key.
- **Curly-apostrophe casings are learned.** `_meaningful_casing` only stripped
  the ASCII `'`, so a correction like `TikTok’s` (Whisper's U+2019) was rejected
  and the casing never stored. All apostrophe glyphs are stripped now.
- **Scratchpad-target route hardened.** The `back` form field is restricted to
  same-site relative paths (no open-redirect / protocol-relative `//evil`), and
  the flash message is URL-encoded so values with `&`/`#`/`=` can't split the
  redirect.
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
- Successful cleanup output (LLM and fallback paths) is now casing/punctuation-
  normalized — previously only the skip-clean fast path was, so model
  Title-Casing could reach the paste buffer untouched. Raw-on-failure,
  `provider: none`, and user-defined transform outputs are left verbatim.
- **Watchdog defers relaunch when a stale PID file can't be removed.**
  Relaunching over an unremovable PID file left it on disk, where a recycled OS
  PID (Windows reuses PIDs) could later read as "alive" and mask a genuine
  crash. The watchdog now skips that tick and retries on the next poll.
- **`open_action_items` has a stable order.** It now orders by
  `(created_at DESC, id DESC)`, so action items extracted within the same second
  no longer shuffle between dashboard requests.
- **"Find similar" surfaces the closest neighbours even when none are positively
  correlated.** `similar_to_id` defaulted to a `0.0` cosine floor, silently
  dropping negatively-correlated rows and contradicting its "always surfaces the
  closest matches" contract; it now uses cosine's `-1.0` floor and lets the
  per-row similarity inform the UI.

### Removed
- Vestigial `ruvector.db` at repo root. No `src/` module referenced it;
  the active vector store is the `embedding` BLOB column on the
  `dictations` table in `data/history.db`.

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
