"""Echo Flow — entry point."""
from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path

from . import log as wlog
from . import notify as wnotify
from . import sound as wsound
wlog.setup()
_log = wlog.get("main")

# Local-only enforcement: warn loudly if any legacy cloud key is set in the
# environment so the user knows we are ignoring it.
BLOCKED_ENV = ["GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
for _k in BLOCKED_ENV:
    if os.getenv(_k):
        _log.warning(
            "%s is set but Echo Flow is local-only. Ignoring.", _k
        )

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


console = Console()


# Rich markup tags (e.g. "[cyan]...[/cyan]") look ugly in the log file.
# This regex strips them before _log.info forwards the message to disk.
import re as _re_strip
_RICH_TAGS = _re_strip.compile(r"\[/?[a-zA-Z0-9_# ]+\]")


def _announce(msg: str, level: str = "info") -> None:
    """Print to terminal AND append to data/wispr.log so silent-VBS runs are debuggable.

    Use for startup events and user-visible milestones — anything you'd want to
    see in a post-mortem when the daemon was running headless.
    """
    console.print(msg)
    plain = _RICH_TAGS.sub("", msg).strip()
    if not plain:
        return
    getattr(_log, level, _log.info)(plain)


def load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
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
            ),
            retriever=self.retriever,
        ) if self.history else None
        # Pattern miner: powers the LLM-free "learned" cleanup provider.
        self.pattern_miner = PatternMiner(hc["db_path"]) if self.history else None
        if self.pattern_miner and self.retriever:
            self.cleaner.attach_learning(self.pattern_miner, self.retriever)

        # Bias the Whisper decoder with the user's custom vocabulary so
        # proper nouns + technical terms are heard correctly the first time.
        # Built once at startup (cached on the Transcriber's WhisperConfig).
        try:
            vocab = self._build_custom_vocabulary()
            if vocab:
                # Keep well under faster-whisper's ~224-token initial_prompt
                # budget. Capping at 80 terms is roughly 100-160 tokens.
                ip = "Vocabulary: " + ", ".join(vocab[:80])
                self.transcriber.cfg.initial_prompt = ip
                _log.info("whisper initial_prompt set with %d vocab terms", min(80, len(vocab)))
        except Exception as e:
            _log.warning("custom vocabulary biasing skipped: %s", e)

        self._mode = cfg["hotkey"].get("mode", "hold")
        self._record_thread: threading.Thread | None = None
        self._active = False
        self._paused = False
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
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        if rms < 0.003:
            console.print(f"[yellow]Too quiet (RMS={rms:.4f}) — likely silence, ignored.[/yellow]")
            return
        if self.tray:
            self.tray.set_state("thinking")
        console.print(f"[dim]Captured {duration_ms} ms (RMS={rms:.3f}) — transcribing…[/dim]")
        t0 = time.time()
        raw, lang, whisper_meta = self.transcriber.transcribe(audio, sr)
        t1 = time.time()
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
        _skipped_before = self.cleaner._n_polish_skipped
        cleaned = self.cleaner.clean(
            raw, style=style, augmentation=augmentation,
            provider_override=provider_override,
            max_tokens_override=max_tokens_override,
            fallback_provider=fallback_provider,
        )
        t2 = time.time()
        polish_skipped = self.cleaner._n_polish_skipped > _skipped_before
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
                        alt_text = self.cleaner.clean_with(alt, raw, style=style, augmentation=augmentation)
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

        # Offline self-grading: produces a 0-100 quality score per dictation.
        # Skip in prompt mode — the rewrite intentionally diverges from raw,
        # which would tank the semantic-coherence score and skew weights.
        if use_prompt:
            self._last_quality = None
        else:
            try:
                quality = grade_mod.grade(
                    raw=raw, cleaned=cleaned, whisper_meta=whisper_meta,
                    retriever=self.retriever, pattern_miner=self.pattern_miner,
                    learner=self.learner, weights=self._grading_weights,
                )
                console.print(
                    f"[cyan]Quality: {quality.overall:.0f} "
                    f"(W:{quality.whisper_conf:.0f} H:{quality.no_hallucination:.0f} "
                    f"S:{quality.semantic_coherence:.0f} P:{quality.pattern_coverage:.0f})"
                    f" — {quality.explanation}[/cyan]"
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

        # PASTE FIRST — user feels the speed.
        self.injector.inject(cleaned)
        t3 = time.time()
        if self.tray:
            self.tray.set_state("ok" if not self._paused else "paused")
        # End-to-end latency instrumentation. t_release is None for the
        # toggle/silence path; in that case skip the e2e number.
        skip_marker = " [polish-skipped]" if polish_skipped else ""
        if t_release is not None:
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

        # THEN log + embed in background so it doesn't add to perceived latency.
        if self.history:
            def _log_async():
                try:
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
                    )
                    if self.learner:
                        self.learner.invalidate_cache()
                    # Mine token-level substitutions for the LLM-free provider.
                    # Skip for prompt mode — the rewrite isn't a cleanup pair.
                    if self.pattern_miner and raw != cleaned and not use_prompt:
                        try:
                            self.pattern_miner.record(raw, cleaned)
                        except Exception as e:
                            print(f"[pattern_miner] failed: {e}")
                except Exception as e:
                    print(f"[log] failed: {e}")
            threading.Thread(target=_log_async, daemon=True).start()

            # Post-process: suggest tags + extract action items.
            # Runs in its own thread so it doesn't block the next dictation.
            def _post_process():
                try:
                    # Wait briefly for _log_async to commit the row so we can
                    # reference its id; if it hasn't yet, skip this round.
                    import time as _t
                    for _ in range(15):
                        if self._last_row_id:
                            break
                        _t.sleep(0.05)
                    if not self._last_row_id:
                        return
                    rid = self._last_row_id
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
        _log.info("hotkey pressed: REC start")
        wsound.play("start", self.cfg.get("sound"))
        console.print("[bold red]● REC[/bold red]")
        if self.tray: self.tray.set_state("rec")
        self.recorder.start()

    def on_release_hold(self):
        if not self._active:
            return
        self._active = False
        t_release = time.time()
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
        console.print(Panel.fit(
            f"[bold]Echo Flow[/bold] — ready\n"
            f"Hotkey: [cyan]{combo}[/cyan]  mode: [cyan]{self._mode}[/cyan]\n"
            f"Tray icon ↗ in system tray for status, pause, edit, history, quit.",
            border_style="green",
        ))
        _log.info("Echo Flow ready (hotkey=%s mode=%s)", combo, self._mode)

        # Tray icon in its own thread
        self.tray = TrayApp(
            get_status=self.tray_status,
            on_pause_toggle=self.tray_pause_toggle,
            on_edit_last=self.tray_edit_last,
            on_open_history=self.tray_open_history,
            on_open_graph=self.tray_open_graph,
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
        def _wire_notify():
            time.sleep(1.0)
            wnotify.set_tray(getattr(self.tray, "_icon", None))
            wnotify.notify("Echo Flow", "Ready. Hold Ctrl+Shift to dictate.", "info")
        threading.Thread(target=_wire_notify, daemon=True).start()

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
