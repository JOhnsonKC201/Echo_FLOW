# Echo Flow — Read-Only Audit

> **Staleness notice (updated 2026-05-29).** This document is a historical
> snapshot from 2026-05-25. Several findings below have since been resolved
> or intentionally reversed. Read the snapshot for the structural map, but
> defer to the per-section deltas in this notice (and to the current
> source) for anything actionable.
>
> **Resolved since 2026-05-25:**
> - **§3 / §4 "Cloud violation" framing is obsolete for the desktop dictation
>   path.** Cloud ASR was removed (`src/transcribe_cloud.py` deleted). The
>   regular hotkey path is local-only. Cloud LLM calls (`_via_groq`,
>   `_via_anthropic`) were re-added but are now scoped to two opt-in
>   surfaces only: Prompt-Engineering mode (`prompt_engineering.provider`)
>   and the teacher-distillation loop (`cleanup.learning.teacher_enabled`,
>   off by default). See `config.yaml:44–93` and `src/cleanup.py:637` (`teach()`).
> - **§9 VRAM budget.** Default polish model is now `qwen2.5:3b-instruct-q4_K_M`
>   (~2 GB), leaving 6 GB headroom for Whisper. The 8 GB over-budget concern
>   was the trigger; see `config.yaml:78`.
> - **§3 Whisper decoder biasing.** `initial_prompt=` is now built from
>   custom vocab + snippets + personal vocabulary. See the
>   `WhisperConfig.initial_prompt` field in `src/transcribe.py`.
> - **§6 Auto-phasing routes to cloud.** Bootstrap / Hybrid phases were
>   deleted. Phases are now `independent` (local Whisper + Ollama) and
>   `self_sufficient` (local Whisper + learned, LLM-free). See `src/phase.py`
>   verbatim — much shorter than the version this audit describes.
> - **§10 Silent exception swallowers.** The flagged sites in `audio.py`,
>   `singleton.py`, `phase.py` were converted to `_log.exception(...)` blocks
>   that preserve the suppression but leave a trace. The hot path still
>   uses guarded `try`/`except` with `_log.warning` as before.
> - **§11 Test count.** Was 9 files / ~960 LOC / claimed "81 tests".
>   Now **43 files / ~6.6K LOC / 529 collected tests**. `tests/eval/`
>   gained an end-to-end latency harness (`measure_e2e.py`) in addition
>   to the existing `run_polish_evals.py`.
>
> **New surface area added since 2026-05-25 (not covered below):**
> - Dashboard (`src/dashboard/`, 22 templates, Flask + PyWebView native window).
> - Teacher-model distillation loop (`Cleaner.teach()` + `source='teacher'`
>   rows + `learned_patterns.user_count` / `teacher_count` columns).
> - Mobile LAN bridge (`src/bridge.py`, loopback-only by default).
> - iOS keyboard extension (`ios/`).
> - PE mode multi-provider support (Groq / Anthropic / Ollama), audience tailoring.
> - Snippet inline editor, custom vocabulary table, notifications inbox,
>   command log, scratchpad, knowledge-graph view.
> - Source tagging on dictations (`desktop` / `mobile` / `teacher`) with
>   trust gates in `LearningConfig.trust_mobile` and `.trust_teacher`.
> - `_audit_cloud_keys()` startup validator + 3 DB performance indexes
>   on `dictations(source, raw_text, style+ts)`.
> - MIT license, GitHub Actions CI workflow.
>
> **Still accurate:** §1 entry-point map (modulo the new modules), §2 audio
> capture, §5 text injection, §7 ruvector (still vestigial — the BLOB
> column on `dictations` remains the live vector store), §8 personalization
> mechanics (now augmented by the teacher loop).
>
> **Observability tooling added 2026-05-29:**
> `scripts/teacher_health.py` prints a teacher-loop health snapshot
> (source counts, acceptance ratio, pattern-origin breakdown, live phase
> decision). `tests/eval/measure_e2e.py` benchmarks per-provider cleanup
> latency (p50 / p95 / p99) across the polish corpus.

Scope: Python desktop dictation app at `C:\Echo_FLOW`. Hardware target: Lenovo Legion Pro 5, Windows, RTX 5060 8GB. Local-only constraint: all cloud calls flagged as violations.

---

## 1. Project map

### `tree /F src`
```
C:\ECHO_FLOW\SRC
    actions.py        editor_cli.py     hotkey.py     notes.py        sound.py            tray.py
    audio.py          grade.py          inject.py     notify.py       tags.py             viewer.py
    cleanup.py        graph.py          learn.py      phase.py        transcribe.py       watchdog.py
    editor.py         history.py        log.py        retrieval.py    transcribe_cloud.py __init__.py
                                        main.py       singleton.py
```
26 modules in `src/`, ~6,200 lines of Python (incl. tests).

### Entry point
`run.bat` activates `.venv` and runs `python -m src.main` (`run.bat:23`). The corresponding entry is `src/main.py:main()` at line 712.

`run_silent.vbs` launches `cmd /c run.bat` with `WISPR_SILENT=1` plus a second process `python -m src.watchdog` for crash auto-restart.

### `requirements.txt` (verbatim)
```
faster-whisper>=1.0.3
sounddevice>=0.4.6
numpy>=1.24
silero-vad>=5.1
pynput>=1.7.6
pyperclip>=1.9.0
pyautogui>=0.9.54
pyyaml>=6.0
requests>=2.31
rich>=13.7
pywin32>=306 ; sys_platform == "win32"
winsdk>=1.0.0b10 ; sys_platform == "win32"
sentence-transformers>=3.0.0
pystray>=0.19.5
Pillow>=10.0
pytest>=8.0
pytest-mock>=3.12
```
Note: `torch` is imported (transcribe.py:25, audio.py:78,104) but not listed — it arrives transitively via `faster-whisper` / `silero-vad` / `sentence-transformers`. CUDA torch build is not pinned, so GPU support depends on whatever pip resolves.

### `config.yaml` (verbatim)
See full file at `config.yaml:1–153`. Key sections:
- `hotkey`: `combo: "ctrl+shift"`, `mode: "hold"`, `paste_last_combo: "ctrl+shift+win"`.
- `audio`: 16000 Hz, mono, VAD on, 1500 ms silence timeout.
- `whisper`: backend `local`, model `auto` (→ `large-v3-turbo` on CUDA, `base` on CPU), device `auto`, compute `auto`, language pinned `en`, `beam_size: 5`, `vad_filter: true`. Has `groq` subsection (cloud).
- `cleanup`: provider `ollama`, model `qwen3.5:latest`, plus `groq` (`llama-3.1-8b-instant`), `anthropic` (`claude-haiku-4-5-20251001`), `openai`, `learned`.
- `prompt_engineering.enabled: true`, `provider: "groq"`, fallback `ollama`, audience `claude-code`.
- `phasing`: 4 phases (bootstrap → hybrid → independent → self_sufficient).

---

## 2. Audio capture

- Library: **sounddevice** (`src/audio.py:10`). Capture is via `sd.InputStream` callback.
- Sample rate: `16000` (`config.yaml:13`).
- Channels: `1` (mono) (`config.yaml:14`).
- Block size: `int(sample_rate * 0.03)` = 480 samples (30 ms) (`src/audio.py:53`).
- dtype: `float32` (`src/audio.py:52`).
- Mode: **Push-to-talk (hold)** is default (`config.yaml:7`). Toggle mode also supported via `record_until_silence()` using Silero VAD (`src/audio.py:72`).
- VAD: optional Silero VAD model loaded at startup (`src/audio.py:33-36`), `prob > 0.5` is "voiced" (`audio.py:108`). Energy-based fallback (`RMS > 0.01`) if VAD missing.

Short/quiet clip guard in `main.py:217-225`: rejects <400 ms or RMS<0.003.

---

## 3. ASR layer

- Engine: **faster-whisper** (`src/transcribe.py:20`). `WhisperModel(...).transcribe(...)`.
- Model: `large-v3-turbo` on CUDA, `base` on CPU (auto-select at `transcribe.py:34-35`). Configurable via `config.yaml:37`.
- Compute type: `float16` on CUDA, `int8` on CPU (`transcribe.py:30`). `auto` is the default.
- Device: `auto` → CUDA if `torch.cuda.is_available()` else CPU (`transcribe.py:23-28`). This is the only CUDA check in the codebase.
- Preloaded at startup: **Yes** — `Transcriber(WhisperConfig(...))` is constructed during `App.__init__` (`main.py:93-100`). Model stays in memory for the daemon lifetime.
- VRAM footprint: `large-v3-turbo` float16 ≈ 1.5–2 GB VRAM (estimate; not measured). `base` int8 on CPU uses RAM, not VRAM.
- Streaming: **No.** Full-utterance. Audio chunks are concatenated on stop() then transcribed in one call (`audio.py:64-70`, `transcribe.py:51-57`).
- `condition_on_previous_text=False` (`transcribe.py:56`) — avoids cross-utterance contamination.
- Returns segment-level grading meta: `avg_logprob`, `no_speech_prob`, `compression_ratio` (`transcribe.py:73-78`).
- Hallucination filter post-transcribe in `main.py:237-244` (drops literal "thank you.", "thanks for watching." on <2 s clips).

### Custom vocabulary / initial_prompt biasing
**No** `initial_prompt=` argument is passed to `faster-whisper.transcribe()` (`transcribe.py:51-57`). Personal vocab and recent corrections are injected only into the **LLM cleanup prompt**, not into the Whisper decoder. See `learn.py:115-156` (`build_prompt_augmentation`). This is a missed opportunity — biasing Whisper directly is cheaper than relying on the LLM to fix proper-noun mishears.

### Cloud ASR — FLAGGED
- `src/transcribe_cloud.py` defines `GroqTranscriber` that POSTs WAV bytes to `https://api.groq.com/openai/v1/audio/transcriptions` (`transcribe_cloud.py:54`). **Cloud violation.**
- `HybridTranscriber` (`transcribe_cloud.py:82`) tries cloud first, falls back to local on exception.
- Activated by `whisper.backend: groq` or `hybrid` in config, OR by `phase.py:179-183` (Phase 1 Bootstrap automatically routes to Groq when GROQ_API_KEY is set AND history <50 dictations).

---

## 4. Polish / LLM layer

- Post-ASR cleanup: **Yes** (`src/cleanup.py`).
- Local LLM serving: **Ollama** via HTTP at `http://localhost:11434/api/chat` (`cleanup.py:391`).
- Configured model: `qwen3.5:latest` (`config.yaml:70`). Default fallback in code is `qwen2.5:7b-instruct` (`cleanup.py:393`). Estimated VRAM at Q4/Q5 ≈ 5–7 GB.
- Quantization: not specified in config — relies on whichever tag Ollama has pulled.

### Two-stage cleanup?
Yes, loosely:
1. **LLM stage** (`cleanup.clean → _via_<provider>`).
2. **Deterministic post-pass**: `_expand_snippets()` (cleanup.py:206-240) expands "btw → by the way", case-aware, word-boundary.
3. The **`learned` provider** (`cleanup.py:427-476`) is a third option that does deterministic-only cleanup: cosine match in past dictations → token substitutions from `learned_patterns` → `_polish_text()` capitalization/punctuation.

### App-aware tone detection
`cleanup.py:242-251` `pick_style(window_title)` substring-matches against `cleanup.profiles` in `config.yaml:105-113`. Maps:
- Code/Cursor/Sublime/PyCharm/Vim → `code` style
- Slack/Discord/Teams/WhatsApp → `casual`
- Gmail/Outlook/Mail → `email`
- else → `default`

Window title comes from Win32 `GetForegroundWindow()` (`inject.py:14-15`).

### System prompts (verbatim)
All five live at `cleanup.py:13-147` (`SYSTEM_PROMPTS` dict): `default`, `code`, `casual`, `email`, `prompt`. The `default` prompt is a ~40-line strict TRANSCRIPT-CLEANER prompt with 9 numbered rules and 4 worked examples. The `prompt` style (Prompt Engineering mode) is the longest (~90 lines) with worked examples covering short/medium/multi-component requests. I did not paste full text here for brevity — they are at:
- `default` → `cleanup.py:14-40`
- `code` → `cleanup.py:41-47`
- `casual` → `cleanup.py:48-51`
- `email` → `cleanup.py:52-56`
- `prompt` → `cleanup.py:63-146`

The Groq user-message wrapper is at `cleanup.py:348-357`. In prompt mode the wrapper is bypassed (`cleanup.py:348`).

### Cloud LLM calls — FLAGGED
1. `_via_groq` → `https://api.groq.com/openai/v1/chat/completions` (`cleanup.py:360`). Cloud.
2. `_via_anthropic` → `https://api.anthropic.com/v1/messages` (`cleanup.py:410`). Cloud.
3. `_via_openai` → `https://api.openai.com/v1/chat/completions` (`cleanup.py:484`). Cloud.
4. `main.py:610-625` pre-warms a HEAD request to `https://api.groq.com/openai/v1/models` at startup if `GROQ_API_KEY` is set.

**Prompt Engineering mode default is Groq** (`config.yaml:121`) with Ollama fallback — every prompt-engineering dictation sends raw user speech to Groq by default. The `cleanup.anthropic.model: claude-haiku-4-5-20251001` is configured but only used if `provider` is set to `anthropic` (not the default).

Auto-phasing (`phase.py:101-192`) means **a fresh install with `GROQ_API_KEY` set automatically uploads audio to Groq AND text to Groq for the first 50 dictations** — without user opt-in.

---

## 5. Text injection

- Method: clipboard paste via `pyperclip` + synthetic `Ctrl+V` via `pyautogui` (`inject.py:40-50`).
- Alternative method: `pyautogui.typewrite` (`inject.py:62-64`) — keyed by `inject.method: "type"`.
- Default: `paste` (`config.yaml:128`).
- Restores clipboard 100ms after paste in a background thread (`inject.py:51-60`).
- Adds trailing space unless text ends in space/newline (`inject.py:33-34`).

Injection function verbatim at `inject.py:30-50` (the `Injector.inject()` and `_paste()` methods).

### Per-app handling
No per-app injection variants — same paste mechanism everywhere. Per-app handling is only in **tone selection** (see §4), not in the injection method.

---

## 6. Background / daemon

- `run_silent.vbs` (6 lines, `run_silent.vbs:1-9`):
  - Sets `WISPR_SILENT=1` env var
  - Launches `cmd /c run.bat` with window hidden (mode 0)
  - Also launches `.venv\Scripts\python.exe -m src.watchdog` (also hidden)
- Hotkey library: **pynput** (`hotkey.py:4`). Global keyboard listener.
- Default hotkey: `ctrl+shift` (hold) (`config.yaml:6`).
- Re-paste hotkey: `ctrl+shift+win` (`config.yaml:10`).
- Prompt-engineering one-shot: `ctrl+shift+alt` (`config.yaml:123`).
- Single-instance lock via TCP bind on port `47823` (`singleton.py:18`). Watchdog uses port `47824` (`watchdog.py:25`).
- Tray icon: **pystray + Pillow** (`tray.py:11-12`). Menu has status line, Pause/Resume, Prompt Engineering toggle, Edit last, Review queue, Pin last, Open history viewer, Open knowledge graph, Quit (`tray.py:82-108`). Icon is a colored microphone glyph (4 states: ok/paused/rec/thinking) generated in `_make_icon()` (`tray.py:15-33`).
- Settings UI: **None**. `config.yaml` is hand-edited.
- Watchdog: polls every 30 s for `data/wispr.pid` liveness via Win32 `OpenProcess` (`watchdog.py:31-58`); relaunches `run_silent.vbs` on death.

Headless? Almost — tray icon is always created (`main.py:628-641`). No console required, but a tray icon is mandatory.

---

## 7. ruvector

There is a file `C:\Echo_FLOW\ruvector.db` (1.5 MB) at repo root. **However, no Python module in `src/` references `ruvector`** — `grep -i ruvector` only hits `.gitignore`, `CHANGELOG.md` ("ruvector.db moved to data/" — but it is still at root), and `scripts/prepare_for_distribution.bat`. The file appears to be a vestigial artifact from an earlier vector-store implementation. The actively-used vector storage is the **`embedding` BLOB column on the `dictations` table** in `data/history.db`.

- What's stored: 384-dim float32 vectors of `raw_text` for every dictation, stored as SQLite BLOB on `dictations.embedding` (`history.py:39`).
- Embedding model: **`sentence-transformers/all-MiniLM-L6-v2`** — 22 MB, English-only, runs on CPU (`retrieval.py:32`). LOCAL.
- Vector dim: **384** (`retrieval.py:46`).
- Distance metric: **Cosine** (dot product of L2-normalized vectors at `retrieval.py:161`).
- Actively used at runtime: **Yes.**
  - Write site: `main.py:368-396` (`_log_async` thread, embeds raw text → BLOB → `history.log(...)`).
  - Read sites:
    - `retrieval.py:133-167` `Retriever.search(query)` — used by `Learner.build_prompt_augmentation` (`learn.py:135-136`) for RAG few-shot example selection, and by `_via_learned` (`cleanup.py:438-447`) for nearest-past-cleaning fallback.
    - `grade.py:84-94` `semantic_coherence(raw, cleaned)` re-embeds both.
    - `notes.py:86-103` for backlinks.
  - Backfill on startup: re-embeds any rows missing vectors or tagged with stale model name (`retrieval.py:90-118`).

**Cloud embedding API calls: None.** sentence-transformers runs locally.

---

## 8. Personalization

- Learns from user corrections: **Yes.**
  - User-edited dictations are stored in `dictations.cleaned_text` (overwritten); original LLM output preserved in `dictations.original_cleaned` (`history.py:49-50`).
  - `learn.PatternMiner.record(raw, cleaned)` mines 1↔1 token substitutions into `learned_patterns` table (`learn.py:229-262`) — called from `main.py:389-393` after each dictation.
  - Confident patterns (`success/total ≥ 0.7`, `total ≥ 2`) feed the `learned` cleanup provider (`learn.py:264-287`, `cleanup.py:452-468`).
  - Patterns decay exponentially with 14-day half-life on each startup (`learn.py:289-318`, called from `main.py:189`).
  - Grading weights `(W,H,S,P)` are calibrated by SGD against user edits on each startup (`grade.py:212-281`).
- Custom dictionary:
  - **Personal vocabulary** mined from recent cleaned text — Capitalized words, CamelCase, snake_case, ALL-CAPS, threshold ≥2 occurrences (`learn.py:77-113`). Cached 60 s. Injected into the LLM prompt only, not into Whisper.
  - **Snippets** (e.g. `btw → by the way`) — defined inline in `config.yaml:91-101`, must be edited by hand.
  - **No** user-facing dictionary editor.
- Per-app preferences: only tone-style mapping (see §4). No per-app vocabulary, hotkey overrides, or behavior toggles.

---

## 9. GPU utilization

- CUDA detection: single call at `transcribe.py:26` `torch.cuda.is_available()`.
- ASR on GPU: yes, if torch CUDA build is installed.
- LLM on GPU: via **Ollama**, which manages its own GPU allocation outside this Python process. The Python code only talks to Ollama via HTTP (`cleanup.py:389-402`).
- Embedding model: **CPU** — sentence-transformers `all-MiniLM-L6-v2` is small enough that the code doesn't explicitly move it to CUDA.
- Silero VAD: runs on CPU via tiny torch tensors (`audio.py:104-108`).

### VRAM budget on RTX 5060 (8 GB)
Estimate (not measured):
- faster-whisper `large-v3-turbo` fp16: ~1.5–2 GB
- Ollama `qwen3.5:latest` (described as "~6.5 GB" at `config.yaml:70`): ~6.5 GB

**Total: ~8–8.5 GB.** This is over budget for an 8 GB card. Either Ollama will swap layers to CPU (latency hit), or the user must downgrade one of the two. The config comment claims qwen3.5 is 9.7B at ~6.5 GB but doesn't address coexistence with Whisper on the same card.

CPU fallback path (`base` Whisper + Ollama on CPU) works but breaks the latency target.

---

## 10. Logging + errors

- Log location: `data/wispr.log`, 5 MB × 5 file rotation (~25 MB max) (`log.py:33-35`).
- Format: `%(asctime)s [%(levelname)-7s] %(name)s: %(message)s` (`log.py:15`).
- File handler: INFO+. Stderr handler: WARNING+ only (so console stays clean) (`log.py:41-43`).
- Logger namespace: `wispr.*`.
- Exception handling:
  - Mixed. Several `except Exception: pass` swallowers remain (audio.py:35-36, audio.py:67, retrieval.py:100-101, retrieval.py:152-154, history.py:60-61, history.py:127-128, history.py:315, phase.py:42-43, phase.py:50-51, phase.py:77-78, phase.py:97-98, singleton.py:27-28,34-35, etc.).
  - The hot path in `main._do_dictation` (~200 lines) is well-instrumented with try/except + `_log.warning` (e.g. main.py:202-204, 320-321, 357-358, 392-395, 427-428).
- Existing latency timers:
  - `t0/t1/t2` in `main.py:229,231,277` measure ASR and cleanup wall-clock per dictation.
  - Console prints "Raw (en, 0.42s):" and "Cleaned (default, 0.18s):" plus log entries.
  - No injection-latency timer or end-to-end timer.

---

## 11. Tests

`tests/` contains 9 test files (excl. `__init__.py` and `conftest.py`), totaling ~960 lines:

- `test_smoke.py` (138 LOC) — config-load, cleanup-empty-input, history round-trip.
- `test_grading.py` (235 LOC) — heaviest. Quality-score math, calibration, weight updates.
- `test_actions.py` (62 LOC) — action-item regex extraction + blocklist.
- `test_tags.py` (109 LOC) — tag suggestion / apply.
- `test_notes.py` (100 LOC) — note promotion + backlinks.
- `test_ab.py` (67 LOC) — A/B shadow logging.
- `test_paste_last.py` (93 LOC) — re-paste cache vs DB.
- `test_snippets.py` (66 LOC) — case-aware snippet expansion.
- `test_veto.py` (87 LOC) — hotkey veto behavior.

Style: unit + light integration (touches real SQLite). No eval-style WER measurement, no live-audio test, no Whisper-quality regression, no end-to-end timed harness.

CHANGELOG claims "81 tests"; actual file count is 9 files (test functions likely ~60–80 — not enumerated).

Coverage estimate: cleanup providers (`_via_groq/_via_ollama/_via_anthropic/_via_openai`) are not tested (would require HTTP mocks). `transcribe.py` and `audio.py` are not tested (require hardware/model). Estimated line coverage ~40–50%.

---

## 12. Known TODOs / gaps

`grep TODO|FIXME|XXX|HACK` across `src/` and `tests/` returns only references inside `actions.py` and `test_actions.py` — the strings appear as data (the action-item extractor's regex looks for the literal word "TODO"), not as code markers. **There are zero TODO/FIXME/HACK/XXX comments in the source code.** Surprising for a project this size; suggests they have been cleaned up or never tracked inline.

CHANGELOG.md is at 0.1.0 with no "Unreleased" section. No incomplete items listed.

### MOBILE_SETUP.md state
`MOBILE_SETUP.md` documents a manual sidecar: install **FUTO Voice Input** (Android, local Whisper) or use **iOS built-in dictation**. There is **no mobile code in this repo** — it is purely a setup guide. The doc itself acknowledges the tradeoff (no LLM cleanup, no learned corrections, no graph) and is explicit that both options are local. No cloud violation for mobile because Echo Flow doesn't run there.

---

## 13. Latency reality check

End-to-end for a 5-second utterance — **all numbers are code-path estimates, not measured.**

**Phase 3 / Independent (local Whisper large-v3-turbo on CUDA + Ollama qwen3.5):**
- Audio stop → drain queue + concatenate: <10 ms (`audio.py:64-70`)
- ASR (large-v3-turbo fp16 on RTX 5060, 5 s clip with beam=5, language pinned): **~400–700 ms** estimate
- Cleanup via Ollama (qwen3.5 ~9.7B, 5–20 token output): **~600–1500 ms** estimate (assumes warm model in VRAM; cold-start adds seconds)
- Inject (clipboard copy + Ctrl+V): ~20 ms (`inject.py:48` has 8 ms sleep + pyautogui latency)
- **Total: ~1.0–2.2 s** before text appears.

**Phase 1 / Bootstrap (Groq Whisper + Groq cleanup):**
- ASR upload + Groq turbo: **~300–500 ms** (Groq is fast; depends on internet)
- Cleanup Groq llama-3.1-8b-instant: **~80–200 ms**
- **Total: ~400–700 ms.** This is faster than fully-local — which is precisely why phasing defaults to it for the first 50 uses.

**Phase 4 / Self-Sufficient (local Whisper + `learned` provider, no LLM):**
- ASR: same ~400–700 ms
- Cleanup (token substitutions + `_polish_text`): **<10 ms**
- **Total: ~450–750 ms.** Fastest local option, but requires 2000+ dictations of history and ≥75 avg quality (`phase.py:124-133`).

Bottleneck on 8 GB VRAM: if Whisper + Ollama can't coexist, Ollama swaps layers, latency balloons to multiple seconds.

---

## 14. Top 3 risks

1. **Local-only constraint is not the default.** Auto-phasing routes a fresh install with `GROQ_API_KEY` set to Groq for ASR *and* cleanup for the first 50 dictations (`phase.py:179-183`). Prompt Engineering mode default provider is `groq` (`config.yaml:121`). The only way to be sure nothing leaves the machine is to unset the env var or set `phasing.enabled: false`. For a "local desktop dictation" pitch, this is the biggest gap.

2. **VRAM budget too tight on 8 GB.** Configured Ollama model `qwen3.5:latest` is ~6.5 GB (per `config.yaml:70` comment) and faster-whisper `large-v3-turbo` fp16 is ~1.5–2 GB — sum exceeds the RTX 5060's 8 GB. Ollama will silently CPU-spill, killing the latency story. No code path measures this or warns the user. (`config.yaml:70`, `transcribe.py:34-35`)

3. **Whisper has no decoder-level biasing.** `faster-whisper.transcribe()` is called without `initial_prompt=` (`transcribe.py:51-57`), so proper-noun mishears can only be corrected downstream by the LLM. The carefully-mined `personal_vocabulary` (`learn.py:77-113`) flows into the LLM prompt but never into the acoustic model — which means the same name gets re-mis-heard on every dictation, then re-cleaned. Cheap fix; currently not done.

Honorable mention: silent `except Exception: pass` blocks in `audio.py:35-36, 66-67`, `singleton.py:27-28,34-35`, `phase.py` — failures here are invisible.

---

## 15. Local-only sanity check

### External network call sites (every one of them)

| File:line | URL | Direction | Notes |
|---|---|---|---|
| `cleanup.py:360` | `https://api.groq.com/openai/v1/chat/completions` | Cloud LLM | `_via_groq` |
| `cleanup.py:410` | `https://api.anthropic.com/v1/messages` | Cloud LLM | `_via_anthropic` |
| `cleanup.py:484` | `https://api.openai.com/v1/chat/completions` | Cloud LLM | `_via_openai` |
| `cleanup.py:391` | `http://localhost:11434/api/chat` | Local | Ollama |
| `transcribe_cloud.py:54` | `https://api.groq.com/openai/v1/audio/transcriptions` | Cloud ASR | `GroqTranscriber.transcribe` |
| `main.py:619` | `https://api.groq.com/openai/v1/models` | Cloud | Pre-warm HEAD at startup if key set |
| `phase.py:48` | `http://localhost:11434/api/tags` | Local | Ollama liveness check |

`requests` is the only HTTP library used (no `httpx`, `aiohttp`, `urllib3` direct calls). No websocket, no MQTT, no telemetry SDK, no Sentry/posthog. sentence-transformers will download the MiniLM model from HuggingFace on first run (one-time, then cached in `~/.cache/huggingface`).

### Can Echo Flow run offline?

**Conditionally yes:**
- If `GROQ_API_KEY` is unset OR `phasing.enabled: false` + `cleanup.provider: ollama|learned|none` + `whisper.backend: local`: fully offline. Confirmed by reading the dispatch logic at `cleanup.py:289-310` and `phase.py:101-192`.
- If Ollama is running locally with the configured model pulled: cleanup works offline.
- sentence-transformers model must be already cached (first run requires HuggingFace download).

**Breaks offline if:**
- First run hasn't downloaded MiniLM yet (`retrieval.py:40-42`).
- First run hasn't downloaded the faster-whisper model (faster-whisper auto-downloads from HuggingFace on first model load).
- `GROQ_API_KEY` is set AND phasing is enabled AND history <50 → auto-routes to Groq, will fail with network error (which `HybridTranscriber.transcribe` does not handle if backend is pure `groq`, see `transcribe_cloud.py:53-59` — will raise on `r.raise_for_status()` and crash the dictation thread).
- Prompt Engineering mode with default config calls Groq first, Ollama fallback (`config.yaml:121-122`, `cleanup.py:326-339` — fallback path works).

**Net assessment:** the app *can* be operated fully offline, but it does not default to that posture. A user who simply installs and runs with a `GROQ_API_KEY` already in their environment will be using cloud services for transcription AND cleanup for their first ~50 dictations without any opt-in prompt.

Audit complete. Ready for external review.
