# Today's changes — 2026-05-25

Seven-phase remediation against `AUDIT.md`. All phases executed in order on
branch `main`. Working tree was Windows / PowerShell, Python 3.13 in
`.venv`, RTX 5060 8 GB, Ollama 0.x with `qwen3.5:latest` and (newly pulled)
`qwen2.5:3b-instruct-q4_K_M`.

## Phase 1 — Safety net (`chore: log silent failures, fix changelog`)
- Replaced `except Exception: pass` blocks with `_log.exception(...)`
  while preserving suppression behavior in:
  - `src/audio.py:35-36, 80-81` (silero_vad load, torch import)
  - `src/singleton.py:27-28, 35-36` (pid write/clear)
  - `src/phase.py:42-43, 50-51, 77-78, 97-98` (sqlite + ollama helpers)
- CHANGELOG "81 tests" claim verified accurate (counted 5+9+9+17+10+10+11+4+6=81),
  so no text edit was needed.
- pytest: 81/81 pass.

## Phase 2 — Rip the cloud (`feat: enforce local-only, remove cloud paths`)
- Deleted `src/transcribe_cloud.py` (Groq Whisper + HybridTranscriber).
- `src/cleanup.py`: removed `_via_groq`, `_via_anthropic`, `_via_openai`;
  dispatcher rewrites legacy provider names (`groq`/`anthropic`/`openai`)
  to `ollama` with a warning. Unused `os` and `json` imports removed.
- `src/main.py`:
  - dropped the `transcribe_cloud` import,
  - replaced the Groq/hybrid backend selection block with a local-only
    `Transcriber` instantiation (and a warning if `whisper.backend !=
    "local"` in config),
  - removed the Groq HTTPS pre-warm thread (`main.py:610-625` in the
    old file),
  - added a startup `BLOCKED_ENV` loop that logs a warning for any of
    `GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (`src/main.py:16-23`).
- `src/phase.py`: rewrote to drop Bootstrap/Hybrid entirely. Phases are
  now just `independent` (local Whisper + Ollama) and `self_sufficient`
  (local Whisper + learned). `decide()` always returns
  `transcribe_backend="local"`. Cloud names in manual config are normalized.
- `config.yaml`:
  - `whisper.groq` block removed.
  - `cleanup.groq` and `cleanup.anthropic` blocks removed.
  - `cleanup.provider` comment updated to "ollama | learned | none".
  - `prompt_engineering.provider: ollama` (was `groq`).
  - `phasing` section simplified — removed `bootstrap_until` and
    `independent_after`.
- `tests/test_smoke.py`: rewrote the two phase tests to assert
  local-only behavior + cloud-name normalization.
- `tests/conftest.py`: `isolated_env` now clears all three cloud API keys.
- `requirements.txt`: no `groq` / `openai` / `anthropic` packages present.
- pytest: 81/81 pass.
- **Not performed by me (user-required):** running `run.bat` and
  dictating a sentence with no `GROQ_API_KEY` to confirm end-to-end UX.

## Phase 3 — Eval harness (`test: add polish eval harness`)
- `tests/eval/polish_evals.yaml`: 30 cases — 10 filler, 8 punctuation,
  5 technical vocab (FastAPI, Supabase, node2vec, PostgreSQL, regex),
  4 tone, 3 edge.
- `tests/eval/run_polish_evals.py`: loads YAML, constructs a `Cleaner`
  from the live `config.yaml` (provider forced to `ollama` for
  measurement), runs each case, scores `contains_all` + `contains_none`
  (+ optional `exact` bonus), prints per-case table and total.
- `tests/eval/baseline_qwen3.5.txt`: baseline run with
  `qwen3.5:latest`. **Result: 50/60 (83.3%) in 1210.6s.** Several cases
  hit the 60s HTTP read timeout — those cases still scored partial
  because the cleaner returned raw text on failure.

## Phase 4 — Smaller model (`perf: switch to qwen2.5:3b for VRAM headroom`)
- `ollama pull qwen2.5:3b-instruct-q4_K_M` succeeded.
- `config.yaml` `cleanup.ollama.model` switched to
  `qwen2.5:3b-instruct-q4_K_M`.
- **First swap eval (old prompt):**
  `tests/eval/swap_qwen2.5-3b.txt` → **53/60 (88.3%) in 12.6s**. Above
  baseline by 5%, ~96× faster. Kept the swap.
- Tightened the `default` system prompt in `src/cleanup.py` per the
  Phase 4d template (shorter, explicit filler list expanded to include
  "basically, well, I mean, I guess, you know what, sort of, kind of").
- **Tightened prompt eval:**
  `tests/eval/swap_qwen2.5-3b_tight.txt` → **56/60 (93.3%) in 5.1s**.
  Kept the tighter prompt as well.

## Phase 5 — Whisper initial_prompt biasing (`feat: bias Whisper decoder with custom vocab`)
- `src/transcribe.py`: added optional `WhisperConfig.initial_prompt`
  field and passed it through to `model.transcribe(...)`.
- `src/main.py`: new `App._build_custom_vocabulary()` merges (in
  priority order) static `config.yaml:custom_vocabulary`, snippet
  expansions from `cleanup.snippets`, and `Learner.personal_vocabulary`.
  Built once at startup, deduped, capped at 80 terms (~100-160 tokens,
  under faster-whisper's ~224-token `initial_prompt` budget), and
  assigned to `self.transcriber.cfg.initial_prompt`.
- `tests/eval/asr_evals.yaml`: 5-case stub marked
  `# requires audio fixtures`. The runner is intentionally not built
  because audio fixtures cannot be recorded headlessly.
- pytest: 81/81 pass.
- **Not performed by me (user-required):** recording or TTS-synthesizing
  audio fixtures, then writing `tests/eval/run_asr_evals.py`.

## Phase 6 — Remove vestigial ruvector (`chore: remove unused ruvector`)
- Verified `ruvector` is referenced only by `.gitignore`, CHANGELOG.md,
  and the distribution script — never by any `src/` module.
- Deleted `C:\Echo_FLOW\ruvector.db` (1.5 MB, already gitignored).
- Did **not** remove `sentence-transformers` from `requirements.txt` —
  it is the embedding model used by `src/retrieval.py`
  (`all-MiniLM-L6-v2`) for the in-`data/history.db` BLOB vector store.
- Added an "Unreleased" section to `CHANGELOG.md` documenting the
  ruvector removal, local-only enforcement, model swap, Whisper biasing,
  and the eval harness; flagged correction-learning personalization as
  planned for a future v0.X release.

## Phase 7 — Verification (`test: verify all phases green`)
- pytest: 81/81 pass.
- Polish eval re-run: `tests/eval/final_qwen2.5-3b_tight.txt` →
  **56/60 (93.3%) in 5.3s**. Above baseline (50/60).
- Latency instrumentation: **YES**, added.
  - `src/main.py` `on_release_hold` now records `t_release = time.time()`
    and passes it into `_do_dictation(audio, t_release)`.
  - Existing `t0`/`t1`/`t2` checkpoints kept (capture-start, asr-done,
    polish-done). New `t3 = time.time()` after `self.injector.inject(...)`.
  - A new `_log.info("latency: release→asr=...ms asr→polish=...ms polish→inject=...ms e2e=...ms", ...)`
    line writes the four checkpoint deltas to `data/wispr.log` per
    dictation. The toggle/silence path (where there is no release event)
    falls back to a three-segment log line.
- Actual end-to-end latency numbers: **NOT measured by me** — measuring
  requires physically holding the hotkey, dictating, and reading
  `data/wispr.log`. The user must run `run.bat`, dictate a sentence,
  and read the latest "latency: ..." line.
- Offline / no-wifi sanity test: **NOT performed by me** — also
  requires interactive setup (disabling wifi). The code path is
  exercised: with cloud paths gone, the only outbound HTTP is to
  `http://localhost:11434/api/{chat,tags}` (Ollama) — both local.

## Phases skipped or reverted

None. All seven phases ran to completion and were committed.

## Open follow-ups for next session

- Record or TTS-synthesize audio fixtures for `tests/eval/asr_evals.yaml`
  and implement `tests/eval/run_asr_evals.py` so initial_prompt biasing
  can be measured quantitatively.
- Run `run.bat` once with no `GROQ_API_KEY` set; confirm UX is identical
  (no surprise toasts, no "Groq disabled" log lines) and capture a real
  end-to-end latency number from the new `latency:` log line.
- The five test_smoke phase tests previously used `monkeypatch.setenv`
  for `GROQ_API_KEY`; now removed. If we ever re-introduce a strict
  "no cloud key at all" assertion, add a dedicated test that imports
  `src.main`'s `BLOCKED_ENV` constant.
- Add an `inject` latency-only timer to the toggle-mode path if/when
  toggle mode becomes a default user flow.
- Long-running history users: consider a one-off migration that drops
  any stale `cleanup.provider = groq` / `anthropic` / `openai` from
  user-edited `config.yaml` files.

All phases complete. Echo Flow is local-only.
