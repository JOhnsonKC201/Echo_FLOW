"""Echo Flow — entry point."""
from __future__ import annotations

import os
import sys
import time
import json
import threading
from pathlib import Path

from . import log as wlog
from . import notify as wnotify
from . import sound as wsound
wlog.setup()
_log = wlog.get("main")

# Cloud-key policy: PE mode (Ctrl+Shift+Alt) and the teacher-distillation
# loop legitimately consume GROQ_API_KEY / ANTHROPIC_API_KEY. The local-only
# guarantee is enforced at the provider-routing layer (src/cleanup.py
# _run_provider), NOT by stripping env vars — which would break opt-in
# cloud features for users who deliberately set the key. We keep OpenAI in
# the blocklist because no code path uses it.
BLOCKED_ENV = ["OPENAI_API_KEY"]
for _k in BLOCKED_ENV:
    if os.getenv(_k):
        _log.warning(
            "%s is set but no Echo Flow feature uses it. Ignoring.", _k
        )
        os.environ.pop(_k, None)

import yaml
from rich.console import Console
from rich.panel import Panel

from .audio import AudioConfig, Recorder
from .transcribe import WhisperConfig, Transcriber
from .cleanup import Cleaner
from .inject import Injector
from .history import History
from .hotkey import HotkeyListener
from .learn import Learner, LearningConfig, PatternMiner
from .retrieval import Retriever, RetrievalConfig
from . import phase as phase_mod
from . import grade as grade_mod
from . import tags as tags_mod
from . import actions as actions_mod
from .tray import TrayApp
from .editor import open_editor
from .viewer import render_history
from .graph import render_graph


console = Console(legacy_windows=False)
# Force UTF-8 on the underlying stdio so any direct print() of em-dashes,
# arrows, or box-drawing chars doesn't crash the daemon on Windows cp1252
# consoles (a recurring class of bug — see _announce's except clauses).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# Rich markup tags (e.g. "[cyan]...[/cyan]") look ugly in the log file.
# This regex strips them before _log.info forwards the message to disk.
import re as _re_strip
_RICH_TAGS = _re_strip.compile(r"\[/?[a-zA-Z0-9_# ]+\]")


def _announce(msg: str, level: str = "info") -> None:
    """Print to terminal AND append to data/wispr.log so silent-VBS runs are debuggable.

    Use for startup events and user-visible milestones — anything you'd want to
    see in a post-mortem when the daemon was running headless.
    """
    try:
        console.print(msg)
    except UnicodeEncodeError:
        # Windows legacy console (cp1252) can't render certain glyphs (→, ✓,
        # etc.) when the daemon is launched without IO redirection. Swallow
        # so a logged arrow doesn't kill the whole daemon.
        pass
    plain = _RICH_TAGS.sub("", msg).strip()
    if not plain:
        return
    getattr(_log, level, _log.info)(plain)


def _transform_combo_to_pynput(combo: str) -> str | None:
    """Convert 'ctrl+alt+p' to pynput GlobalHotKeys format '<ctrl>+<alt>+p'."""
    if not combo:
        return None
    mods = {"ctrl", "alt", "shift", "win", "cmd"}
    parts = combo.lower().split("+")
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            return None
        if p == "win":
            out.append("<cmd>")
        elif p in mods:
            out.append(f"<{p}>")
        elif p.startswith("f") and p[1:].isdigit():
            out.append(f"<{p}>")
        elif len(p) == 1:
            out.append(p)
        else:
            return None
    return "+".join(out)


# User-data root. In dev (running from source) this is the repo. When frozen
# by PyInstaller the executable lives somewhere read-only (e.g. Program Files
# or %LOCALAPPDATA%\EchoFlow installed by Inno Setup), and user-writable state
# — config.yaml, data/, logs — must live in %LOCALAPPDATA%\EchoFlow.
if getattr(sys, "frozen", False):
    USER_ROOT = Path(os.environ.get(
        "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
    )) / "EchoFlow"
    USER_ROOT.mkdir(parents=True, exist_ok=True)
    # Read-only bundled resources sit in _MEIPASS.
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    USER_ROOT = Path(__file__).resolve().parent.parent
    BUNDLE_ROOT = USER_ROOT

CONFIG_PATH = USER_ROOT / "config.yaml"


def _audit_cloud_keys(cfg: dict) -> None:
    """Warn loudly when a cloud feature is enabled but its API key is missing.

    Echo Flow stays local-only by default, but the user can opt into:
      - Prompt-Engineering mode (cloud LLM for the next dictation)
      - Teacher-distillation loop (background re-cleanup by a stronger LLM)
    Both need an API key set in the environment. If we don't notice the
    misconfiguration here, the first failure surfaces as a cryptic runtime
    error mid-dictation.
    """
    pe = cfg.get("prompt_engineering", {}) or {}
    learning = ((cfg.get("cleanup") or {}).get("learning") or {})
    needs: dict[str, list[str]] = {}
    if pe.get("enabled") and pe.get("provider") == "groq":
        needs.setdefault("GROQ_API_KEY", []).append("Prompt-Engineering mode")
    if pe.get("enabled") and pe.get("provider") == "anthropic":
        needs.setdefault("ANTHROPIC_API_KEY", []).append("Prompt-Engineering mode")
    if learning.get("teacher_enabled"):
        needs.setdefault("GROQ_API_KEY", []).append("Teacher distillation")
    for env_key, features in needs.items():
        if not os.environ.get(env_key, "").strip():
            _log.warning(
                "%s is required by: %s — but it is not set in the environment. "
                "Set it via `setx %s your-key-here` (Windows) and restart the "
                "daemon. The affected feature will silently fall back until then.",
                env_key, ", ".join(features), env_key,
            )


def _format_initial_prompt(terms: list[str]) -> str:
    """Build a Whisper initial_prompt that doesn't poison output style.

    faster-whisper feeds initial_prompt to the decoder as "previous text",
    so the decoder will continue that style. Two failure modes to avoid:
      1. Comma-separated lists ("foo, bar, baz") → Whisper produces
         comma-separated output ("Hello, world, today.")
      2. Bare label prefixes ("Vocabulary:") → Whisper sometimes echoes
         the label.
    We wrap the terms in a complete sentence ending with a period so the
    decoder treats them as words seen in fluent prose, not as a list.
    Space-separated only — no commas anywhere in the prompt.
    """
    if not terms:
        return ""
    # Dedupe while preserving order; strip empties.
    seen = set()
    cleaned: list[str] = []
    for t in terms:
        t = (t or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
    if not cleaned:
        return ""
    return "The speaker often uses words like " + " ".join(cleaned) + "."


def load_config() -> dict:
    # First-run on a frozen install: seed user config from the bundled default.
    if not CONFIG_PATH.exists() and (BUNDLE_ROOT / "config.yaml").exists():
        import shutil
        shutil.copy(BUNDLE_ROOT / "config.yaml", CONFIG_PATH)
    # Frozen builds: chdir to user root so all relative paths in cfg
    # (history.db_path, data/dashboard.port, data/wispr.log, …) land in the
    # user-writable dir instead of next to the read-only .exe.
    if getattr(sys, "frozen", False):
        try:
            os.chdir(USER_ROOT)
        except Exception:
            pass
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class App:
    def __init__(self, cfg: dict, cfg_path: Path | None = None):
        self.cfg = cfg
        self.cfg_path = cfg_path or CONFIG_PATH
        # Shared lock between desktop hotkey path and mobile HTTP bridge —
        # faster-whisper + Ollama HTTP client aren't guaranteed thread-safe
        # on a single model instance. Cheap when only one path is active.
        self._pipeline_lock = threading.RLock()
        # Cloud-feature key audit: surface clear, actionable warnings at
        # startup so users discover misconfiguration before their first
        # dictation. Each warning maps to a documented setup step.
        _audit_cloud_keys(cfg)
        # Auto-phase: decide backend BEFORE loading anything
        db_path = cfg["history"]["db_path"]
        self.phase = phase_mod.decide(cfg, db_path)
        _announce(
            f"[magenta]Phase: {self.phase.name}[/magenta] — {self.phase.reason}"
        )
        # Apply phase decision over static config
        cfg["whisper"]["backend"] = self.phase.transcribe_backend
        cfg["cleanup"]["provider"] = self.phase.cleanup_provider
        ac = cfg["audio"]
        self.recorder = Recorder(AudioConfig(
            sample_rate=ac["sample_rate"],
            channels=ac["channels"],
            device=ac.get("device"),
            vad_enabled=ac.get("vad_enabled", True),
            silence_timeout_ms=ac.get("silence_timeout_ms", 1500),
        ))
        wc = cfg["whisper"]
        backend = wc.get("backend", "local")
        if backend != "local":
            _log.warning(
                "whisper.backend=%s is a legacy cloud setting; Echo Flow is "
                "local-only. Forcing backend=local.", backend,
            )
            backend = "local"
            wc["backend"] = "local"
        _announce(f"[cyan]Loading local Whisper: {wc['model']}…[/cyan]")
        self.transcriber = Transcriber(WhisperConfig(
            model=wc["model"],
            device=wc.get("device", "auto"),
            compute_type=wc.get("compute_type", "auto"),
            language=wc.get("language"),
            beam_size=wc.get("beam_size", 5),
            vad_filter=wc.get("vad_filter", True),
        ))
        self.cleaner = Cleaner(cfg["cleanup"])
        # Preload the cleanup model in the background so the first dictation
        # doesn't pay the cold-start cost (5-15s for qwen2.5:3b).
        threading.Thread(target=self.cleaner.warmup, daemon=True).start()
        ic = cfg["inject"]
        self.injector = Injector(
            method=ic.get("method", "paste"),
            restore_clipboard=ic.get("restore_clipboard", True),
            trailing_space=ic.get("trailing_space", True),
        )
        hc = cfg["history"]
        self.history = History(hc["db_path"]) if hc.get("enabled") else None

        lc = cfg["cleanup"].get("learning", {})
        rc = lc.get("retrieval", {})
        self.retriever = None
        if self.history and rc.get("enabled", True):
            self.retriever = Retriever(
                hc["db_path"],
                RetrievalConfig(
                    enabled=True,
                    k=lc.get("max_examples", 6),
                    min_similarity=rc.get("min_similarity", 0.35),
                    backfill_on_startup=rc.get("backfill_on_startup", True),
                ),
            )
            threading.Thread(target=self.retriever.warm, daemon=True).start()
        self.learner = Learner(
            hc["db_path"],
            LearningConfig(
                enabled=lc.get("enabled", True),
                max_examples=lc.get("max_examples", 6),
                max_vocab_terms=lc.get("max_vocab_terms", 25),
                min_example_chars=lc.get("min_example_chars", 12),
                trust_mobile=bool(lc.get("trust_mobile", False)),
                trust_teacher=bool(lc.get("trust_teacher", True)),
            ),
            retriever=self.retriever,
        ) if self.history else None
        # Pattern miner: powers the LLM-free "learned" cleanup provider.
        self.pattern_miner = PatternMiner(hc["db_path"]) if self.history else None
        if self.pattern_miner and self.retriever:
            self.cleaner.attach_learning(self.pattern_miner, self.retriever)
        # Periodic pattern-decay: prune stale learned_patterns nightly so the
        # learned provider stays sharp instead of accumulating dead weight.
        if self.pattern_miner:
            self._start_pattern_decay_thread()

        # Wire dashboard-managed snippets into the cleaner so user edits in
        # the Snippets UI take effect on the next dictation. Falls back to
        # the static config.yaml mapping when the SQLite table is empty.
        if self.history is not None:
            try:
                from .dashboard import snippets as _sn
                config_snips = (self.cfg.get("cleanup") or {}).get("snippets") or {}
                self.cleaner.set_snippets_provider(
                    lambda h=self.history, d=config_snips: _sn.merged_snippet_map(h.conn, d)
                )
            except Exception as e:
                _log.warning("snippet provider wiring failed: %s", e)
            try:
                from .dashboard import style_profiles as _sp
                self.cleaner.set_style_provider(
                    lambda title, h=self.history: _sp.pick_style(h.conn, title, config_default="")
                )
            except Exception as e:
                _log.warning("style provider wiring failed: %s", e)

        # Bias the Whisper decoder with the user's custom vocabulary so
        # proper nouns + technical terms are heard correctly the first time.
        # Built once at startup (cached on the Transcriber's WhisperConfig).
        #
        # CRITICAL: initial_prompt is fed to the decoder as "previous text"
        # — Whisper continues that style. A comma-separated list teaches
        # Whisper to emit comma-separated output ("Hello, world, today.").
        # We therefore use SPACE-separated terms wrapped in a complete
        # sentence that ends with a period, so the decoder doesn't anchor
        # on list punctuation.
        try:
            vocab = self._build_custom_vocabulary()
            if vocab:
                # Keep well under faster-whisper's ~224-token initial_prompt
                # budget. Capping at 80 terms is roughly 100-160 tokens.
                self.transcriber.cfg.initial_prompt = _format_initial_prompt(vocab[:80])
                _log.info("whisper initial_prompt set with %d vocab terms", min(80, len(vocab)))
        except Exception as e:
            _log.warning("custom vocabulary biasing skipped: %s", e)

        self._mode = cfg["hotkey"].get("mode", "hold")
        self._record_thread: threading.Thread | None = None
        self._active = False
        self._paused = False
        # M8: focused_title() involves a Win32 round-trip (~3-15ms in some
        # configurations). Cache at hotkey-press time so the hot path between
        # ASR and cleanup doesn't pay for it. Consumed + cleared per dictation.
        self._press_title: str | None = None
        # Prompt Engineering mode: sticky toggle from tray, plus a one-shot
        # flag armed by an optional hotkey and consumed on the next dictation.
        self._pe_cfg = cfg.get("prompt_engineering", {"enabled": False})
        self._prompt_mode = False
        self._prompt_oneshot = False
        self._last_row_id: int | None = None
        # Most-recent cleaned text held in RAM so re-paste isn't racing the
        # async DB write (which lands ~100-300ms after we paste).
        self._last_cleaned_text: str | None = None
        self.tray: TrayApp | None = None
        # Dashboard-armed transform consumed on the next dictation; reset after use.
        self._armed_transform: dict | None = None
        self._transform_hotkey_listener = None
        # Dashboard-armed scratchpad target. When non-None, the next
        # dictations append to that scratchpad's body instead of injecting
        # into the focused window. Cleared explicitly via the UI.
        self._scratchpad_target_id: int | None = None
        # Self-grading state
        self._grading_weights = grade_mod.load_weights(hc["db_path"]) if self.history else None
        self._recent_qualities: list[float] = []
        self._last_quality = None
        # One-shot self-improvement pass at startup:
        #   1. Decay stale learned patterns (so old habits fade)
        #   2. Log calibration correlation (diagnostic)
        #   3. Update grading weights from accumulated user edits
        if self.history:
            def _self_improve():
                try:
                    decayed, deleted = self.pattern_miner.decay_stale() if self.pattern_miner else (0, 0)
                    if decayed:
                        _log.info("pattern decay: %d patterns aged, %d forgotten", decayed, deleted)
                    r = grade_mod.calibrate_from_edits(hc["db_path"])
                    if r is not None:
                        _log.info("self-grading calibration: r=%.3f (negative is good)", r)
                    new_w = grade_mod.update_weights_from_edits(hc["db_path"])
                    if new_w:
                        _log.info(
                            "weight update: W=%.2f H=%.2f S=%.2f P=%.2f",
                            new_w["W"], new_w["H"], new_w["S"], new_w["P"],
                        )
                        # Reload so this session uses the updated weights.
                        self._grading_weights = new_w
                except Exception as e:
                    _log.warning("self-improve pass failed: %s", e)
            threading.Thread(target=_self_improve, daemon=True).start()

    def refresh_transform_hotkeys(self) -> None:
        """(Re)register pynput GlobalHotKeys for every transform with a hotkey.

        Tears down any prior listener and starts fresh. Pressing a registered
        combo arms self._armed_transform for the NEXT dictation — the cleanup
        path consumes it and reverts to default behavior on subsequent calls.
        """
        # Tear down prior listener if any.
        prior = getattr(self, "_transform_hotkey_listener", None)
        if prior is not None:
            try:
                prior.stop()
            except Exception:
                pass
            self._transform_hotkey_listener = None
        history = getattr(self, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return
        try:
            from .dashboard import transforms as _tf
            items = _tf.list_transforms(history.conn)
        except Exception as e:
            _log.warning("refresh_transform_hotkeys: list failed: %s", e)
            return
        # Build {pynput_combo_string: callback} map.
        bindings: dict[str, callable] = {}
        for t in items:
            if not t.get("enabled") or not t.get("hotkey"):
                continue
            combo = _transform_combo_to_pynput(t["hotkey"])
            if not combo:
                continue
            tid = t["id"]
            def _arm(tid=tid):
                self._arm_transform(tid)
            bindings[combo] = _arm
        if not bindings:
            return
        try:
            from pynput import keyboard as _kb
            listener = _kb.GlobalHotKeys(bindings)
            listener.start()
            self._transform_hotkey_listener = listener
            _log.info("transform hotkeys: %d bindings active", len(bindings))
        except Exception as e:
            _log.warning("transform hotkey listener failed: %s", e)

    def _arm_transform(self, transform_id: int) -> None:
        """Called by a transform hotkey — arm the next dictation."""
        history = getattr(self, "history", None)
        if history is None:
            return
        try:
            from .dashboard import transforms as _tf
            t = _tf.get_transform(history.conn, transform_id)
        except Exception as e:
            _log.warning("arm_transform: get failed: %s", e)
            return
        if t is None or not t.get("enabled"):
            return
        self._armed_transform = t
        _log.info("transform armed for next dictation: %s", t["name"])
        try:
            from . import notify as _n
            _n.notify("Echo Flow", f"Transform armed: {t['name']}", "info")
        except Exception:
            pass

    def reload_config(self) -> None:
        """Rebuild hot-reloadable state after a dashboard config mutation.

        Currently re-derives the Whisper initial_prompt (so dictionary
        additions take effect on the next dictation without a daemon
        restart). Other settings (mic device, hotkey, model) still
        require a full restart — the dashboard surfaces that as a banner.
        """
        try:
            vocab = self._build_custom_vocabulary()
            ip = _format_initial_prompt(vocab[:80]) if vocab else None
            if hasattr(self.transcriber, "cfg"):
                self.transcriber.cfg.initial_prompt = ip
                _log.info(
                    "reload_config: initial_prompt refreshed with %d terms",
                    len(vocab[:80]),
                )
            self._pe_cfg = self.cfg.get("prompt_engineering", {"enabled": False})
            # Refresh learner trust flags so dashboard toggles (trust_teacher,
            # trust_mobile) take effect on the next dictation without a restart.
            if self.learner is not None:
                lc = ((self.cfg.get("cleanup") or {}).get("learning") or {})
                self.learner.cfg.trust_mobile = bool(lc.get("trust_mobile", False))
                self.learner.cfg.trust_teacher = bool(lc.get("trust_teacher", True))
                self.learner.invalidate_cache()
        except Exception as e:
            _log.warning("reload_config failed: %s", e)

    def _build_custom_vocabulary(self) -> list[str]:
        """Assemble the vocabulary list used to bias the Whisper decoder.

        Merges (in priority order):
          1. Static custom_vocabulary list from config.yaml (if present)
          2. Snippet expansions (config.yaml cleanup.snippets values) so
             snippet outputs land in the acoustic prior too.
          3. Personal vocabulary mined from history via Learner.

        De-duplicates while preserving order, returning at most ~80 terms.
        """
        terms: list[str] = []
        seen: set[str] = set()

        def _add(t: str) -> None:
            t = (t or "").strip()
            if not t or t.lower() in seen:
                return
            seen.add(t.lower())
            terms.append(t)

        # 1. Optional static list from config (top-level custom_vocabulary).
        static = self.cfg.get("custom_vocabulary") or []
        if isinstance(static, list):
            for t in static:
                _add(str(t))

        # 1b. Dashboard-managed terms (custom_vocabulary table). Highest user
        # intent — user typed these explicitly through the Dictionary UI.
        history = getattr(self, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                from .dashboard import vocabulary as _vocab
                for t in _vocab.all_terms(history.conn):
                    _add(t)
            except Exception as e:
                _log.warning("dashboard custom_vocabulary read failed: %s", e)

        # 2. Snippet expansion targets.
        snippets = (self.cfg.get("cleanup") or {}).get("snippets") or {}
        for v in snippets.values():
            _add(str(v))

        # 3. Personal vocabulary mined from history.
        if self.learner is not None:
            try:
                for t in self.learner.personal_vocabulary(limit=80):
                    _add(t)
            except Exception as e:
                _log.warning("personal_vocabulary failed: %s", e)

        return terms[:80]

    def _do_dictation(self, audio, t_release: float | None = None):
        if self._paused:
            console.print("[dim]Paused — discarding audio.[/dim]")
            return
        if audio.size == 0:
            console.print("[yellow]No audio captured.[/yellow]")
            return
        # Bug fix: reject too-short clips before sending to Whisper.
        # Whisper hallucinates "Thank you" / "Thanks for watching" on silence.
        sr = self.cfg["audio"]["sample_rate"]
        duration_ms = int(len(audio) / sr * 1000)
        if duration_ms < 400:
            console.print(f"[yellow]Too short ({duration_ms}ms) — ignored.[/yellow]")
            return
        import numpy as np
        # Recorder.stop() already returns float32; skip the redundant copy.
        audio_f32 = audio if audio.dtype == np.float32 else audio.astype(np.float32)
        rms = float(np.sqrt(np.mean(audio_f32 ** 2)))
        if rms < 0.003:
            console.print(f"[yellow]Too quiet (RMS={rms:.4f}) — likely silence, ignored.[/yellow]")
            return
        if self.tray:
            self.tray.set_state("thinking")
        console.print(f"[dim]Captured {duration_ms} ms (RMS={rms:.3f}) — transcribing…[/dim]")
        t0 = time.perf_counter()
        with self._pipeline_lock:
            raw, lang, whisper_meta = self.transcriber.transcribe(audio, sr)
        t1 = time.perf_counter()
        console.print(f"[green]Raw ({lang}, {t1-t0:.2f}s):[/green] {raw}")
        _log.info("raw (%s, %.2fs): %s", lang, t1 - t0, raw)
        if not raw.strip():
            return
        # Whisper hallucination filter: very common phrases on silence/noise
        HALLUCINATIONS = {
            "thank you.", "thanks for watching.", "thanks for watching!",
            "you", ".", "thank you", "thanks.", "bye.", "you're welcome.",
            "i'm sorry.", "thank you so much.",
        }
        if raw.strip().lower() in HALLUCINATIONS and duration_ms < 2000:
            console.print(f"[yellow]Likely Whisper hallucination on silence — dropped.[/yellow]")
            if self.tray: self.tray.set_state("ok")
            return

        # M8: prefer the title captured at hotkey-press time (no Win32 call
        # on the hot path). Fall back to a live lookup if nothing was cached
        # (e.g. mobile-bridge entry point that doesn't go through a hotkey).
        title = self._press_title
        self._press_title = None
        if title is None:
            title = self.injector.focused_title()
        style = self.cleaner.pick_style(title)

        # Prompt Engineering mode: tray-sticky toggle OR one-shot hotkey.
        # Overrides style + provider, and disables RAG/A-B/pattern-mining for
        # this dictation since those infer from cleanup pairs, not rewrites.
        use_prompt = bool(self._pe_cfg.get("enabled")) and (
            self._prompt_mode or self._prompt_oneshot
        )
        if use_prompt:
            style = "prompt"
            self._prompt_oneshot = False  # consume the one-shot arm
            console.print("[magenta]🪄 Prompt Engineering mode active for this dictation.[/magenta]")
            wnotify.notify(
                "Echo Flow",
                "Prompt-Engineering mode armed — your next dictation will be rewritten via Groq.",
                "info",
            )

        # Skip RAG augmentation for short inputs (no benefit) AND for prompt
        # mode (past cleanup examples derail the rewrite).
        skip_aug = use_prompt or len(raw) < 25
        augmentation = (
            self.learner.build_prompt_augmentation(style, query_text=raw)
            if (self.learner and not skip_aug) else ""
        )
        provider_override = self._pe_cfg.get("provider") if use_prompt else None
        max_tokens_override = self._pe_cfg.get("max_output_tokens") if use_prompt else None
        fallback_provider = self._pe_cfg.get("fallback_provider") if use_prompt else None
        # Armed transform from a dashboard-bound hotkey — overrides system
        # prompt for this dictation only, then auto-disarms. Doesn't fire
        # when use_prompt is active (PE mode takes precedence).
        system_prompt_override = None
        armed = getattr(self, "_armed_transform", None)
        if armed and not use_prompt:
            system_prompt_override = armed["system_prompt"]
            console.print(f"[magenta]✨ Transform armed: {armed['name']}[/magenta]")
            self._armed_transform = None
            # RAG augmentation makes no sense for an arbitrary transform.
            augmentation = ""
        # Pipeline lock: shared with mobile HTTP bridge to prevent concurrent
        # Whisper/Ollama hits on a single model instance.
        with self._pipeline_lock:
            cleaned, polish_skipped = self.cleaner.clean(
                raw, style=style, augmentation=augmentation,
                provider_override=provider_override,
                max_tokens_override=max_tokens_override,
                fallback_provider=fallback_provider,
                system_prompt_override=system_prompt_override,
            )
        t2 = time.perf_counter()
        # Cache immediately so Ctrl+Shift+Win re-paste sees this dictation
        # even before the async DB write commits.
        self._last_cleaned_text = cleaned

        # A/B provider shadow test: runs the alternate provider on the same
        # raw text in a background thread, grades both, and logs the
        # comparison. We still paste the primary (predictable UX).
        ab_cfg = self.cfg.get("cleanup", {}).get("ab_test", {})
        if ab_cfg.get("enabled") and self.history and not use_prompt and not polish_skipped:
            alt = ab_cfg.get("alternate", "learned")
            primary = self.cleaner.provider
            if alt and alt != primary:
                def _shadow():
                    try:
                        alt_text, _ = self.cleaner.clean_with(alt, raw, style=style, augmentation=augmentation)
                        # Grade both outputs (same whisper_meta — only H/S/P differ).
                        prim_q = grade_mod.grade(raw, cleaned, whisper_meta,
                                                  retriever=self.retriever,
                                                  pattern_miner=self.pattern_miner,
                                                  learner=self.learner,
                                                  weights=self._grading_weights).overall
                        alt_q = grade_mod.grade(raw, alt_text, whisper_meta,
                                                 retriever=self.retriever,
                                                 pattern_miner=self.pattern_miner,
                                                 learner=self.learner,
                                                 weights=self._grading_weights).overall
                        if prim_q > alt_q + 1.0:
                            winner = primary
                        elif alt_q > prim_q + 1.0:
                            winner = alt
                        else:
                            winner = "tie"
                        self.history.log_ab(
                            raw_text=raw,
                            primary_provider=primary, primary_text=cleaned, primary_quality=prim_q,
                            alt_provider=alt, alt_text=alt_text, alt_quality=alt_q,
                            winner=winner,
                        )
                        _log.info(
                            "AB %s vs %s: primary=%.1f alt=%.1f → winner=%s",
                            primary, alt, prim_q, alt_q, winner,
                        )
                    except Exception as e:
                        _log.warning("ab shadow failed: %s", e)
                threading.Thread(target=_shadow, daemon=True).start()
        learn_marker = ""
        if augmentation:
            learn_marker = " [+RAG]" if "SEMANTICALLY" in augmentation else " [+learning]"
        console.print(f"[green]Cleaned ({style}, {t2-t1:.2f}s){learn_marker}:[/green] {cleaned}")
        _log.info("cleaned (%s, %.2fs)%s: %s", style, t2 - t1, learn_marker, cleaned)

        # Grading is deferred to _log_async below so it doesn't block paste.
        # In prompt mode we still clear _last_quality up-front because the
        # grading thread will skip it (rewrites legitimately diverge from raw).
        if use_prompt:
            self._last_quality = None

        # Scratchpad routing: when a scratchpad is armed via dashboard, append
        # cleaned text to its body instead of pasting into the focused window.
        # Falls through to normal inject on any failure.
        # Phase 13 + 14: Command Mode and Action Mode share the prefix word
        # (default "computer"). Command Mode runs FIRST (keystrokes are higher-
        # precision, lower-risk); on a no-match it falls through to Action Mode.
        # Both always return BEFORE inject so nothing gets pasted behind them.
        # Only when both miss under the prefix do we notify "unknown".
        exp_cfg = self.cfg.get("experimental", {}) or {}
        prefix = exp_cfg.get("command_prefix", "computer")
        cmd_unmatched = False   # prefix present but no Command Mode hit
        cmd_body = ""

        # The command/action prefix is a control token, not prose — so detect it
        # on the RAW transcript first. The cleanup pass strips filler words and
        # repunctuates, which can silently swallow a prefix like "hey" (or
        # rewrite "computer, open X" → "Open X.") and hide the command from
        # classify(). Prefer raw when it carries the prefix; else use cleaned.
        cmd_text = cleaned
        if exp_cfg.get("command_mode") or exp_cfg.get("action_mode"):
            from . import commands as _commands
            if _commands.strip_prefix(raw, prefix) is not None:
                cmd_text = raw

        if exp_cfg.get("command_mode"):
            body = _commands.strip_prefix(cmd_text, prefix)
            if body:   # non-empty: a bare "computer," (no command) is ignored
                result = _commands.classify(body)
                if result is not None:
                    action_type, action_value, label = result
                    ok = (self.injector.send_key(action_value)
                          if action_type == "key"
                          else self.injector.send_hotkey(action_value))
                    _log.info("voice command: %s (%s %s) ok=%s",
                              label, action_type, action_value, ok)
                    if self.history is not None:
                        try:
                            self.history.log_command(
                                body=body, action_type=action_type,
                                action_value=action_value, label=label, ok=ok,
                            )
                        except Exception as e:
                            _log.warning("log_command failed: %s", e)
                    if self.tray:
                        self.tray.set_state("ok" if not self._paused else "paused")
                    return
                # No keystroke matched — remember it and fall through to Action
                # Mode instead of declaring "unknown" here.
                cmd_unmatched = True
                cmd_body = body

        # Phase 14: Action Mode — semantic handlers (open app / url / web search)
        # behind the same prefix. Reached either directly (command_mode off) or
        # via Command Mode fallthrough.
        if exp_cfg.get("action_mode"):
            from . import voice_actions as _va
            # Prefix path: an explicit wake-word ("hey/computer, open X") shows
            # intent, so a miss is reported as "unknown".
            # Prefix-free path (action_require_prefix: false): act on a bare
            # verb ("open spotify") ONLY when it confidently resolves — a
            # configured app/folder or a valid URL/search. Anything else falls
            # through and is typed normally, so plain dictation is never eaten.
            body = _va.strip_prefix(cmd_text, prefix)
            prefixed = bool(body)   # empty body (bare "jarvis,") is not a command
            match = None
            if prefixed:
                match = _va.classify(body, self.cfg)
            elif not exp_cfg.get("action_require_prefix", True):
                free_body = (raw or cleaned or "").strip()
                # Try the LITERAL utterance first, then the same utterance with a
                # (possibly mis-heard) leading wake word stripped — "Zalvis open
                # email" → "open email". Literal-first means a prefix that sounds
                # like a real verb can't mis-strip and reroute. Both are fail-safe:
                # fire only when the candidate resolves, so a false strip can never
                # swallow plain dictation.
                fuzzy = _commands.strip_prefix_fuzzy(free_body, prefix)
                seen: set[str] = set()
                for cb in (free_body, fuzzy):
                    if not cb or cb in seen:
                        continue
                    seen.add(cb)
                    cand = _va.classify(cb, self.cfg)
                    if cand is not None and _va.resolves(cand, self.cfg, self.history):
                        match, body = cand, cb
                        break
            if match is not None or prefixed:
                if match is not None:
                    ctx = _va.ActionContext(
                        focused_title=title,
                        focused_path=self.injector.focused_document_path(),
                        cfg=self.cfg, notify=wnotify.notify,
                        cleaner=self.cleaner, history=self.history,
                        injector=self.injector,
                    )
                    ok, msg = _va.dispatch(match, ctx)
                    _log.info("voice action: %s (%s) ok=%s", match.label, match.name, ok)
                    wnotify.notify("Echo Flow", msg, "info" if ok else "warning")
                    if self.history is not None:
                        try:
                            # SEC-3: redact sensitive args (queries, note bodies,
                            # URLs → host) unless verbose logging is opted in.
                            verbose = exp_cfg.get("action_log_verbose")
                            args_for_log = (
                                match.args if verbose
                                else _va.redact_args(match.name, match.args)
                            )
                            # `body` is the full spoken utterance — redact it too
                            # unless verbose, so the raw transcription doesn't land
                            # at-rest / on the dashboard next to already-redacted args.
                            body_for_log = (
                                body if verbose
                                else "<redacted len=%d>" % len(body or "")
                            )
                            self.history.log_action(
                                body=body_for_log, handler=match.name,
                                args_json=json.dumps(args_for_log),
                                label=match.label, ok=ok,
                                error=None if ok else msg,
                            )
                        except Exception as e:
                            _log.warning("log_action failed: %s", e)
                    if self.tray:
                        self.tray.set_state("ok" if not self._paused else "paused")
                    return   # actions never leave a paste behind
                # Prefix present but no action matched → report as unknown.
                # (Unreachable when unprefixed: the guard above only lets a
                # prefixed miss fall through here.)
                cmd_unmatched = True
                cmd_body = body

        if cmd_unmatched:
            wnotify.notify("Echo Flow", f"Unknown voice command: {cmd_body[:40]}", "warning")
            if self.history is not None:
                try:
                    self.history.log_command(
                        body=cmd_body, action_type="unknown",
                        action_value="", label=None, ok=False,
                    )
                except Exception as e:
                    _log.warning("log_command failed: %s", e)
            if self.tray:
                self.tray.set_state("ok" if not self._paused else "paused")
            return

        pad_target = getattr(self, "_scratchpad_target_id", None)
        if pad_target and self.history is not None:
            try:
                from .dashboard import scratchpad as _spad
                ok = _spad.append_to_scratchpad(self.history.conn, pad_target, cleaned)
                if ok:
                    console.print(f"[cyan]→ appended to scratchpad #{pad_target}[/cyan]")
                else:
                    self.injector.inject(cleaned)
            except Exception as e:
                _log.warning("scratchpad append failed: %s — falling back to inject", e)
                self.injector.inject(cleaned)
        else:
            # Phase 12: trailing voice command. If experimental.press_enter_command
            # is on and the dictation ends with "press enter" / "submit" / "send it",
            # paste the stripped text first, then fire Enter. Skipped when
            # routing to a scratchpad — that path doesn't inject keystrokes.
            payload = cleaned
            trailing_cmd: str | None = None
            if (self.cfg.get("experimental", {}) or {}).get("press_enter_command"):
                from . import actions as _actions
                detected = _actions.detect_trailing_command(cleaned)
                if detected is not None:
                    trailing_cmd, payload = detected
            # PASTE FIRST — user feels the speed. When firing a trailing
            # command, suppress the auto-appended trailing space so Enter
            # lands right after the final character of the payload.
            if trailing_cmd is not None:
                prev_ts = self.injector.trailing_space
                self.injector.trailing_space = False
                try:
                    self.injector.inject(payload)
                finally:
                    self.injector.trailing_space = prev_ts
            else:
                self.injector.inject(payload)
            if trailing_cmd == "enter":
                self.injector.send_key("enter")
                _log.info("trailing-command: enter fired")
            if use_prompt:
                wnotify.notify(
                    "Echo Flow",
                    "Prompt rewritten via Groq → pasted.",
                    "info",
                )
        t3 = time.perf_counter()
        if self.tray:
            self.tray.set_state("ok" if not self._paused else "paused")
        # End-to-end latency instrumentation. t_release is None for the
        # toggle/silence path; in that case skip the e2e number.
        skip_marker = " [polish-skipped]" if polish_skipped else ""
        latency_e2e_ms: int | None = None
        if t_release is not None:
            latency_e2e_ms = int((t3 - t_release) * 1000)
            _log.info(
                "latency: release→asr=%.0fms asr=%.0fms polish=%.0fms inject=%.0fms e2e=%.0fms%s",
                (t0 - t_release) * 1000,
                (t1 - t0) * 1000,
                (t2 - t1) * 1000,
                (t3 - t2) * 1000,
                (t3 - t_release) * 1000,
                skip_marker,
            )
        else:
            _log.info(
                "latency: asr=%.0fms polish=%.0fms inject=%.0fms%s",
                (t1 - t0) * 1000,
                (t2 - t1) * 1000,
                (t3 - t2) * 1000,
                skip_marker,
            )

        # THEN log + embed + grade in background so they don't add to
        # perceived latency. The tray polls _last_quality, so it's OK that
        # the score lands a few hundred ms after the paste.
        if self.history:
            # Per-dictation handoff of the logged row id from _log_async to
            # _post_process. Using a closure-local box (not the shared
            # self._last_row_id) prevents a second, overlapping dictation from
            # clobbering the id between commit and read — which would attach
            # THIS dictation's tags/action-items to the wrong row.
            _row_ready = threading.Event()
            _row_box: list[int] = []
            def _log_async():
                try:
                    # H5: grade off the hot path. Skip in prompt mode — the
                    # rewrite intentionally diverges from raw, which would
                    # tank the semantic-coherence score and skew the weights.
                    if not use_prompt:
                        try:
                            quality = grade_mod.grade(
                                raw=raw, cleaned=cleaned, whisper_meta=whisper_meta,
                                retriever=self.retriever, pattern_miner=self.pattern_miner,
                                learner=self.learner, weights=self._grading_weights,
                            )
                            if quality.overall < 50:
                                wnotify.notify(
                                    "Echo Flow",
                                    f"Low-confidence dictation ({quality.overall:.0f}/100) — review?",
                                    "warning",
                                )
                            self._last_quality = quality
                            self._recent_qualities.append(quality.overall)
                            if len(self._recent_qualities) > 50:
                                self._recent_qualities = self._recent_qualities[-50:]
                        except Exception as e:
                            _log.warning("grading failed: %s", e)
                            self._last_quality = None
                    emb_blob = None
                    if self.retriever:
                        vec = self.retriever.embed_text(raw)
                        if vec is not None:
                            from .retrieval import to_blob
                            emb_blob = to_blob(vec)
                    model_name = self.retriever.model_name() if self.retriever else None
                    q_score = self._last_quality.overall if self._last_quality else None
                    q_breakdown = self._last_quality.to_json() if self._last_quality else None
                    self._last_row_id = self.history.log(
                        window_title=title, style=style, language=lang,
                        duration_ms=duration_ms, raw_text=raw, cleaned_text=cleaned,
                        embedding=emb_blob, embedding_model=model_name,
                        quality_score=q_score, quality_breakdown=q_breakdown,
                        latency_ms=latency_e2e_ms,
                    )
                    # Hand THIS row id to _post_process (see _row_box above).
                    _row_box.append(self._last_row_id)
                    _row_ready.set()
                    if self.learner:
                        self.learner.invalidate_cache()
                    # Mine token-level substitutions for the LLM-free provider.
                    # Skip for prompt mode — the rewrite isn't a cleanup pair.
                    if self.pattern_miner and raw != cleaned and not use_prompt:
                        try:
                            self.pattern_miner.record(raw, cleaned)
                        except Exception as e:
                            print(f"[pattern_miner] failed: {e}")
                    # Background teacher distillation: re-clean via a stronger
                    # cloud model and store as source='teacher'. Off by default;
                    # opt in via cleanup.learning.teacher_enabled.
                    if not use_prompt:
                        self._spawn_teacher_distillation(
                            raw=raw, user_cleaned=cleaned, style=style,
                            window_title=title, lang=lang, duration_ms=duration_ms,
                        )
                except Exception as e:
                    print(f"[log] failed: {e}")
            threading.Thread(target=_log_async, daemon=True).start()

            # Post-process: suggest tags + extract action items.
            # Runs in its own thread so it doesn't block the next dictation.
            def _post_process():
                try:
                    # Wait briefly for _log_async to commit THIS dictation's row
                    # and publish its id; if it doesn't within the window, skip.
                    if not _row_ready.wait(timeout=0.75) or not _row_box:
                        return
                    rid = _row_box[0]
                    # Tag suggestions
                    sugg = tags_mod.suggest_tags(
                        cleaned, retriever=self.retriever, history=self.history,
                        cluster_label=None,   # cluster info not threaded here yet
                    )
                    if sugg:
                        tags_mod.apply_suggestions(self.history, rid, sugg)
                        _log.info("tags suggested for #%d: %s", rid,
                                   ", ".join(f"{s.name}({s.confidence:.2f})" for s in sugg))
                    # Action items
                    items = actions_mod.extract_action_items(cleaned)
                    for item in items:
                        self.history.add_action_item(rid, item)
                    if items:
                        _log.info("action items for #%d: %s", rid, " | ".join(items))
                except Exception as e:
                    _log.warning("post-process failed: %s", e)
            threading.Thread(target=_post_process, daemon=True).start()

    # --- hold mode ---
    def on_press_hold(self):
        if self._active or self._paused:
            return
        self._active = True
        # M8: capture focused window title NOW, before recording. This is the
        # window the user intends to dictate into; capturing later (after ASR)
        # races with focus changes and costs a Win32 round-trip on the hot path.
        try:
            self._press_title = self.injector.focused_title()
        except Exception:
            self._press_title = None
        _log.info("hotkey pressed: REC start")
        wsound.play("start", self.cfg.get("sound"))
        console.print("[bold red]● REC[/bold red]")
        if self.tray: self.tray.set_state("rec")
        self.recorder.start()

    def on_release_hold(self):
        if not self._active:
            return
        self._active = False
        t_release = time.perf_counter()
        audio = self.recorder.stop()
        _log.info("hotkey released: stop, captured %d samples", len(audio))
        wsound.play("stop", self.cfg.get("sound"))
        console.print("[bold]■ stop[/bold]")
        threading.Thread(
            target=self._do_dictation, args=(audio, t_release), daemon=True
        ).start()

    def on_cancel_hold(self):
        """Veto: another combo (e.g. Ctrl+Shift+Win) is forming — abort recording."""
        if not self._active:
            return
        self._active = False
        try:
            self.recorder.stop()   # discard whatever was captured
        except Exception as e:
            _log.warning("recorder.stop on cancel failed: %s", e)
        if self.tray:
            self.tray.set_state("ok")
        _log.info("dictation aborted: veto key pressed (likely re-paste combo)")
        console.print("[dim]■ aborted[/dim]")

    # --- toggle mode ---
    def on_toggle(self):
        if self._active or self._paused:
            return
        self._active = True
        # M8: cache focused title at press time (see on_press_hold).
        try:
            self._press_title = self.injector.focused_title()
        except Exception:
            self._press_title = None
        wsound.play("start", self.cfg.get("sound"))
        console.print("[bold red]● REC (auto-stop on silence)[/bold red]")
        if self.tray: self.tray.set_state("rec")

        def _run():
            audio = self.recorder.record_until_silence(max_seconds=120.0)
            self._active = False
            wsound.play("stop", self.cfg.get("sound"))
            console.print("[bold]■ stop[/bold]")
            self._do_dictation(audio)
        threading.Thread(target=_run, daemon=True).start()

    # --- tray callbacks ---
    def tray_status(self) -> dict:
        n = 0
        try:
            if self.history:
                cur = self.history.conn.execute("SELECT COUNT(*) FROM dictations")
                n = int(cur.fetchone()[0])
        except Exception:
            pass
        avg_q = (
            sum(self._recent_qualities) / len(self._recent_qualities)
            if self._recent_qualities else None
        )
        ab = None
        if self.cfg.get("cleanup", {}).get("ab_test", {}).get("enabled") and self.history:
            try:
                ab = self.history.ab_tally(since_seconds=7 * 86400)
            except Exception:
                ab = None
        return {
            "phase": self.phase.name,
            "dictations": n,
            "paused": self._paused,
            "avg_quality": avg_q,
            "ab_tally": ab,
        }

    def tray_pause_toggle(self):
        self._paused = not self._paused
        console.print(f"[yellow]{'⏸ Paused' if self._paused else '▶ Resumed'}[/yellow]")
        if self.tray:
            self.tray.set_state("paused" if self._paused else "ok")

    def tray_edit_last(self):
        # Tk must run on its own main thread; spawn a subprocess so it gets one.
        db = self.cfg["history"]["db_path"]
        row_arg = str(self._last_row_id) if self._last_row_id else "last"
        import subprocess
        subprocess.Popen(
            [sys.executable, "-m", "src.editor_cli", db, row_arg],
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def tray_open_history(self):
        db = self.cfg["history"]["db_path"]
        threading.Thread(target=render_history, args=(db,), daemon=True).start()

    def tray_open_graph(self):
        db = self.cfg["history"]["db_path"]
        threading.Thread(target=render_graph, args=(db,), daemon=True).start()

    @staticmethod
    def _format_hotkey_label(combo: str) -> str:
        """'<ctrl>+<cmd>' → 'Ctrl + Win' for tray display. Empty if unset."""
        if not combo:
            return ""
        parts = combo.replace("<", "").replace(">", "").split("+")
        pretty = []
        for p in parts:
            low = p.lower()
            if low == "cmd":
                pretty.append("Win")
            elif low == "ctrl":
                pretty.append("Ctrl")
            elif low == "alt":
                pretty.append("Alt")
            elif low == "shift":
                pretty.append("Shift")
            else:
                pretty.append(p.upper() if len(p) == 1 else p.title())
        return " + ".join(pretty)

    def _start_pattern_decay_thread(self) -> None:
        """Daemon thread that decays learned_patterns once every 24h.

        Calls PatternMiner.decay_stale() with the configured half-life so
        patterns the user stops reinforcing fade out instead of persisting
        forever. Sleep-based loop; quietly no-ops on errors.
        """
        lc = ((self.cfg.get("cleanup") or {}).get("learned") or {})
        half_life = float(lc.get("pattern_half_life_days", 14.0))
        interval_sec = float(lc.get("pattern_decay_interval_sec", 24 * 3600))
        if half_life <= 0 or interval_sec <= 0:
            return

        def _loop():
            import time as _t
            # Wait once on startup so the daemon comes up fast.
            _t.sleep(min(interval_sec, 300.0))
            while True:
                try:
                    pruned, kept = self.pattern_miner.decay_stale(half_life_days=half_life)
                    _log.info("pattern decay: pruned=%d kept=%d (half-life %.1fd)",
                              pruned, kept, half_life)
                except Exception as e:
                    _log.warning("pattern decay failed: %s", e)
                _t.sleep(interval_sec)

        threading.Thread(target=_loop, daemon=True, name="pattern-decay").start()

    def _spawn_teacher_distillation(self, *, raw: str, user_cleaned: str,
                                    style: str, window_title: str,
                                    lang: str, duration_ms: int) -> None:
        """Schedule a background teacher cleanup of `raw` and persist it.

        Off by default. Enabled via cleanup.learning.teacher_enabled. Runs in
        a daemon thread so it never blocks the live dictation path. The
        teacher's output is stored as a second dictations row with
        source='teacher' and also fed to the PatternMiner, so the LLM-free
        learned provider benefits from both the user's edits and a stronger
        cloud model's edits.
        """
        if not self.history or not self.pattern_miner or not self.cleaner:
            return
        lc = ((self.cfg.get("cleanup") or {}).get("learning") or {})
        if not lc.get("teacher_enabled", False):
            return
        if not raw or not raw.strip():
            return
        # Cost / rate guards: skip trivially-short dictations and respect
        # the user-configured sample rate so heavy use doesn't burn quota.
        min_chars = int(lc.get("teacher_min_chars", 30))
        if len(raw.strip()) < min_chars:
            return
        try:
            sample_rate = float(lc.get("teacher_sample_rate", 1.0))
        except (TypeError, ValueError):
            sample_rate = 1.0
        if sample_rate < 1.0:
            import random as _r
            if _r.random() > max(0.0, sample_rate):
                return

        def _run():
            try:
                teacher_out = self.cleaner.teach(raw, style=style)
                if not teacher_out or teacher_out == raw or teacher_out == user_cleaned:
                    return
                # Snippet-aware: run snippet expansion on the teacher's output
                # so it's directly comparable to the user's cleaned text and
                # so PatternMiner doesn't learn around snippet triggers.
                try:
                    teacher_out = self.cleaner._expand_snippets(teacher_out)
                except Exception:
                    pass
                # Quality gate: grade both and only persist when the teacher
                # is at least as good as the user's local cleanup. Prevents
                # a flaky teacher from poisoning the learning pool.
                require_gate = bool(lc.get("teacher_quality_gate", True))
                if require_gate:
                    try:
                        from . import grade as grade_mod
                        user_q = grade_mod.grade(
                            raw=raw, cleaned=user_cleaned, whisper_meta=None,
                            retriever=self.retriever, pattern_miner=self.pattern_miner,
                            learner=self.learner, weights=self._grading_weights,
                        )
                        teach_q = grade_mod.grade(
                            raw=raw, cleaned=teacher_out, whisper_meta=None,
                            retriever=self.retriever, pattern_miner=self.pattern_miner,
                            learner=self.learner, weights=self._grading_weights,
                        )
                        if teach_q.overall < user_q.overall - 0.5:
                            _log.info(
                                "teacher rejected by quality gate "
                                "(user=%.1f teacher=%.1f)",
                                user_q.overall, teach_q.overall,
                            )
                            return
                    except Exception as e:
                        _log.warning("teacher quality gate failed (allowing): %s", e)
                try:
                    self.history.log(
                        window_title=window_title,
                        style=style,
                        language=lang,
                        duration_ms=duration_ms,
                        raw_text=raw,
                        cleaned_text=teacher_out,
                        source="teacher",
                    )
                except Exception as e:
                    _log.warning("teacher history log failed: %s", e)
                try:
                    self.pattern_miner.record(raw, teacher_out, source="teacher")
                except Exception as e:
                    _log.warning("teacher pattern_miner.record failed: %s", e)
                if self.learner:
                    self.learner.invalidate_cache()
            except Exception as e:
                _log.warning("teacher distillation thread failed: %s", e)

        threading.Thread(target=_run, daemon=True).start()

    def tray_open_dashboard(self):
        """Launch the PyWebView dashboard window in a detached subprocess.

        Falls back to opening the default browser if the window can't open
        (PyWebView missing, WebView2 runtime absent, etc.). The window
        process is independent of the daemon — closing it won't stop
        dictation, and a crash won't take the daemon down.
        """
        import subprocess
        try:
            from . import dashboard as _dash
            port = _dash.read_port_file() or int(
                self.cfg.get("dashboard", {}).get("port", 8766)
            )
        except Exception:
            port = 8766

        venv_python = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
        py = str(venv_python) if venv_python.exists() else sys.executable

        def _spawn():
            try:
                subprocess.Popen(
                    [py, "-m", "src.dashboard.window", "--port", str(port)],
                    cwd=str(Path(__file__).resolve().parent.parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as e:
                _log.warning("dashboard window launch failed: %s; opening in browser", e)
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{port}/")

        threading.Thread(target=_spawn, daemon=True).start()

    def _on_paste_last(self):
        """Re-paste handler: prefers the in-memory cache (no race with async DB write),
        falls back to the database for newly-started sessions."""
        if self._paused or self._active:
            return
        # Prefer in-memory: avoids the async DB-write race.
        text = self._last_cleaned_text
        if not text and self.history:
            try:
                row = self.history.conn.execute(
                    "SELECT cleaned_text FROM dictations "
                    "WHERE cleaned_text IS NOT NULL AND cleaned_text != '' "
                    "ORDER BY ts DESC LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    text = row[0]
            except Exception as e:
                _log.warning("paste-last query failed: %s", e)
        if not text:
            wnotify.notify("Echo Flow", "No dictation to re-paste yet.", "info")
            return
        _log.info("re-paste last: %d chars", len(text))
        wsound.play("stop", self.cfg.get("sound"))
        # Small delay so any lingering physical-key state from Ctrl+Shift+Win
        # clears before we synthesize Ctrl+V via pyautogui.
        time.sleep(0.06)
        self.injector.inject(text)
        if self.tray:
            self.tray.set_state("ok")

    def tray_open_review_queue(self):
        # Tk must run on its own main thread; subprocess gives it one.
        db = self.cfg["history"]["db_path"]
        import subprocess
        subprocess.Popen(
            [sys.executable, "-m", "src.editor_cli", db, "queue"],
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def tray_toggle_prompt_mode(self):
        """Tray menu: toggle sticky Prompt Engineering mode."""
        self._prompt_mode = not self._prompt_mode
        state = "ON" if self._prompt_mode else "off"
        console.print(f"[magenta]🪄 Prompt Engineering: {state}[/magenta]")
        _log.info("prompt_mode toggled: %s", state)
        wnotify.notify("Echo Flow", f"Prompt Engineering: {state}", "info")

    def tray_get_prompt_mode_state(self) -> bool:
        return self._prompt_mode

    def tray_pin_last(self):
        """Pin the most recent dictation as a Note via a quick Tk dialog."""
        db = self.cfg["history"]["db_path"]
        import subprocess
        subprocess.Popen(
            [sys.executable, "-m", "src.editor_cli", db, "pin-last"],
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def tray_quit(self):
        console.print("[dim]Quit requested from tray.[/dim]")
        os._exit(0)

    def run(self):
        combo = self.cfg["hotkey"]["combo"]
        try:
            console.print(Panel.fit(
                f"[bold]Echo Flow[/bold] — ready\n"
                f"Hotkey: [cyan]{combo}[/cyan]  mode: [cyan]{self._mode}[/cyan]\n"
                f"Tray icon ↗ in system tray for status, pause, edit, history, quit.",
                border_style="green",
            ))
        except UnicodeEncodeError:
            # Windows legacy console (cp1252) can't render —/↗; the daemon
            # logs the same info to wispr.log via the _log.info call below.
            pass
        _log.info("Echo Flow ready (hotkey=%s mode=%s)", combo, self._mode)

        # Tray icon in its own thread
        self.tray = TrayApp(
            get_status=self.tray_status,
            on_pause_toggle=self.tray_pause_toggle,
            on_edit_last=self.tray_edit_last,
            on_open_history=self.tray_open_history,
            on_open_graph=self.tray_open_graph,
            on_open_dashboard=self.tray_open_dashboard,
            dashboard_hotkey_label=self._format_hotkey_label(
                self.cfg.get("dashboard", {}).get("open_hotkey", "")),
            on_open_review_queue=self.tray_open_review_queue,
            on_pin_last=self.tray_pin_last,
            on_toggle_prompt_mode=self.tray_toggle_prompt_mode
                if self._pe_cfg.get("enabled") else None,
            get_prompt_mode_state=self.tray_get_prompt_mode_state
                if self._pe_cfg.get("enabled") else None,
            on_quit=self.tray_quit,
        )
        threading.Thread(target=self.tray.run, daemon=True).start()
        # Give pystray a moment to create its icon, then wire it into notify
        # Phase 9: persist every notify() call to the inbox so the dashboard's
        # bell badge has a durable log. Sink is best-effort — toasts must work
        # even if history is disabled.
        if getattr(self, "history", None) is not None and getattr(self.history, "conn", None) is not None:
            from .dashboard import notifications as _nf_inbox
            def _sink(level, title, body):
                try:
                    _nf_inbox.insert(self.history.conn, level, title, body)
                except Exception:
                    pass
            wnotify.set_sink(_sink)

        def _wire_notify():
            time.sleep(1.0)
            wnotify.set_tray(getattr(self.tray, "_icon", None))
            wnotify.notify("Echo Flow", "Ready. Hold Ctrl+Shift to dictate.", "info")
        threading.Thread(target=_wire_notify, daemon=True).start()

        # Mobile bridge: optional HTTP server so the user's phone can hit the
        # same pipeline over local Wi-Fi (see docs/MOBILE_BRIDGE.md).
        mobile_cfg = self.cfg.get("mobile", {})
        if mobile_cfg.get("enabled"):
            try:
                from . import bridge as _bridge
                key = _bridge.ensure_shared_key(self.cfg, self.cfg_path)
                host = mobile_cfg.get("bind_address", "0.0.0.0")
                port = int(mobile_cfg.get("port", 8765))
                threading.Thread(
                    target=_bridge.serve,
                    args=(self, host, port, _announce),
                    daemon=True,
                ).start()
                if mobile_cfg.get("advertise_mdns", True):
                    self._mdns_handle = _bridge.advertise_mdns(host, port)
                # Do NOT print the full key — the bridge banner already shows
                # a truncated prefix. Full value lives in config.yaml.
            except Exception as e:
                _log.warning("mobile bridge failed to start: %s", e)

        # Bind transform hotkeys (Phase 6) — pulls from dashboard transforms
        # table. Idempotent; safe to call after every dashboard mutation too.
        try:
            self.refresh_transform_hotkeys()
        except Exception as e:
            _log.warning("initial transform hotkey wiring failed: %s", e)

        # Desktop dashboard: local Flask app for the Wispr-style sidebar UI.
        # Bound to 127.0.0.1 by default — same-machine trust model, no auth.
        # Daemon thread so the hotkey/audio path is never blocked by it.
        dash_cfg = self.cfg.get("dashboard", {})
        if dash_cfg.get("enabled", True):
            try:
                from . import dashboard as _dash
                dhost = dash_cfg.get("host", "127.0.0.1")
                dport = int(dash_cfg.get("port", 8766))
                threading.Thread(
                    target=_dash.serve,
                    args=(self, dhost, dport, _announce),
                    daemon=True,
                ).start()
            except Exception as e:
                _log.warning("dashboard failed to start: %s", e)

        # Global hotkey for "Open Dashboard" — pops the pywebview window from
        # anywhere without needing a tray click. Independent listener so it
        # doesn't perturb the dictation hotkey or the transform registry.
        open_combo = dash_cfg.get("open_hotkey", "<ctrl>+<alt>+<space>")
        if open_combo:
            try:
                from pynput import keyboard as _kb
                self._dashboard_hotkey_listener = _kb.GlobalHotKeys(
                    {open_combo: self.tray_open_dashboard})
                self._dashboard_hotkey_listener.start()
                _log.info("dashboard open hotkey: %s", open_combo)
            except Exception as e:
                _log.warning("dashboard open hotkey %r failed: %s", open_combo, e)

        # Secondary listener: re-paste last dictation on Ctrl+Win (or whatever
        # paste_last_combo is configured to). Runs in its own thread so it
        # doesn't compete with the primary listener's blocking join.
        paste_combo = self.cfg["hotkey"].get("paste_last_combo")
        if paste_combo:
            try:
                # Fire on RELEASE, not press — by the time the user lets go of
                # Ctrl+Shift+Win, the modifier state is clear and the synthetic
                # Ctrl+V from the injector can land in the focused window.
                paste_listener = HotkeyListener(
                    paste_combo, "hold",
                    on_activate=lambda: None,     # no-op on press
                    on_deactivate=self._on_paste_last,
                )
                threading.Thread(target=paste_listener.run, daemon=True).start()
                _announce(f"[dim]Re-paste hotkey: {paste_combo} (fires on release)[/dim]")
            except Exception as e:
                _log.warning("paste-last listener failed to start: %s", e)

        # Prompt-engineering one-shot hotkey: arms _prompt_oneshot so the very
        # next dictation goes through the prompt-engineering pipeline, even if
        # the tray toggle is off. Fires on release for the same reason as the
        # paste-last listener — modifier state needs to clear first.
        oneshot_combo = self._pe_cfg.get("oneshot_combo") if self._pe_cfg.get("enabled") else None
        if oneshot_combo:
            try:
                def _arm_prompt_oneshot():
                    if self._prompt_oneshot:
                        return  # already armed
                    self._prompt_oneshot = True
                    _log.info("prompt-engineering one-shot armed")
                    wnotify.notify(
                        "Echo Flow",
                        "🪄 Prompt Engineering armed for next dictation",
                        "info",
                    )
                pe_listener = HotkeyListener(
                    oneshot_combo, "hold",
                    on_activate=lambda: None,
                    on_deactivate=_arm_prompt_oneshot,
                )
                threading.Thread(target=pe_listener.run, daemon=True).start()
                _announce(f"[dim]Prompt-engineering one-shot hotkey: {oneshot_combo}[/dim]")
            except Exception as e:
                _log.warning("prompt-engineering listener failed to start: %s", e)

        # The dictation listener vetoes when "win" is added — that means the
        # user is forming the re-paste combo (Ctrl+Shift+Win), not dictating.
        veto = "win" if paste_combo and "win" in paste_combo.lower() else None
        if self._mode == "hold":
            listener = HotkeyListener(
                combo, "hold", self.on_press_hold, self.on_release_hold,
                veto_keys=veto, on_veto=self.on_cancel_hold,
            )
        else:
            listener = HotkeyListener(
                combo, "toggle", self.on_toggle,
                veto_keys=veto, on_veto=self.on_cancel_hold,
            )
        listener.run()


def main():
    # Single-instance guard — if another daemon is running, exit immediately.
    from .singleton import acquire_or_exit
    acquire_or_exit()
    cfg = load_config()
    try:
        app = App(cfg)
        app.run()
    except KeyboardInterrupt:
        console.print("\n[dim]bye.[/dim]")
        sys.exit(0)


if __name__ == "__main__":
    main()
