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

import yaml
from rich.console import Console
from rich.panel import Panel

from .audio import AudioConfig, Recorder
from .transcribe import WhisperConfig, Transcriber
from .transcribe_cloud import GroqTranscriber, GroqWhisperConfig, HybridTranscriber
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
        local = None
        cloud = None
        if backend in ("local", "hybrid"):
            _announce(f"[cyan]Loading local Whisper: {wc['model']}…[/cyan]")
            local = Transcriber(WhisperConfig(
                model=wc["model"],
                device=wc.get("device", "auto"),
                compute_type=wc.get("compute_type", "auto"),
                language=wc.get("language"),
                beam_size=wc.get("beam_size", 5),
                vad_filter=wc.get("vad_filter", True),
            ))
        else:
            _announce("[dim]Skipping local Whisper (groq-only mode = fast)[/dim]")
        if backend in ("groq", "hybrid"):
            gc = wc.get("groq", {})
            try:
                _announce(f"[cyan]Using Groq Whisper: {gc.get('model', 'whisper-large-v3-turbo')}[/cyan]")
                cloud = GroqTranscriber(GroqWhisperConfig(
                    model=gc.get("model", "whisper-large-v3-turbo"),
                    language=wc.get("language"),
                    api_key_env=gc.get("api_key_env", "GROQ_API_KEY"),
                ))
            except Exception as e:
                _announce(f"[yellow]Groq disabled: {e}[/yellow]", level="warning")
                cloud = None
        if backend == "groq" and cloud:
            self.transcriber = cloud
        elif backend == "hybrid" and cloud and local:
            self.transcriber = HybridTranscriber(cloud, local)
        elif local:
            self.transcriber = local
        elif cloud:
            self.transcriber = cloud
        else:
            raise RuntimeError("No transcription backend available")
        self.cleaner = Cleaner(cfg["cleanup"])
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

        self._mode = cfg["hotkey"].get("mode", "hold")
        self._record_thread: threading.Thread | None = None
        self._active = False
        self._paused = False
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

    def _do_dictation(self, audio):
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

        # Skip RAG augmentation for short inputs — they don't benefit and the
        # extra prompt tokens add ~50-100ms latency.
        skip_aug = len(raw) < 25
        augmentation = (
            self.learner.build_prompt_augmentation(style, query_text=raw)
            if (self.learner and not skip_aug) else ""
        )
        cleaned = self.cleaner.clean(raw, style=style, augmentation=augmentation)
        t2 = time.time()
        # Cache immediately so Ctrl+Shift+Win re-paste sees this dictation
        # even before the async DB write commits.
        self._last_cleaned_text = cleaned

        # A/B provider shadow test: runs the alternate provider on the same
        # raw text in a background thread, grades both, and logs the
        # comparison. We still paste the primary (predictable UX).
        ab_cfg = self.cfg.get("cleanup", {}).get("ab_test", {})
        if ab_cfg.get("enabled") and self.history:
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
        if self.tray:
            self.tray.set_state("ok" if not self._paused else "paused")

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
                    if self.pattern_miner and raw != cleaned:
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
        audio = self.recorder.stop()
        _log.info("hotkey released: stop, captured %d samples", len(audio))
        wsound.play("stop", self.cfg.get("sound"))
        console.print("[bold]■ stop[/bold]")
        threading.Thread(target=self._do_dictation, args=(audio,), daemon=True).start()

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

        # Pre-warm Groq HTTPS connection so first dictation is fast (~200ms saved)
        def _prewarm():
            try:
                import os, requests
                key = os.environ.get("GROQ_API_KEY")
                if not key:
                    return
                # HEAD on a real endpoint establishes TLS + keeps it pooled
                self.cleaner._session.head(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=5,
                )
            except Exception:
                pass
        threading.Thread(target=_prewarm, daemon=True).start()

        # Tray icon in its own thread
        self.tray = TrayApp(
            get_status=self.tray_status,
            on_pause_toggle=self.tray_pause_toggle,
            on_edit_last=self.tray_edit_last,
            on_open_history=self.tray_open_history,
            on_open_graph=self.tray_open_graph,
            on_open_review_queue=self.tray_open_review_queue,
            on_pin_last=self.tray_pin_last,
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
