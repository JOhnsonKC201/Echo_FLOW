"""Phase 8 — Settings panels: General / System / Vibe / Experimental / Privacy.

All saves flow through dashboard.config_writer.set_scalar so config.yaml's
comments survive. Each panel renders the current value live from app_ref.cfg
so cancelled or partial edits don't leave the UI out of sync.

The General panel surfaces a "restart required" banner since hotkey,
language, and audio device are not hot-reloadable.
"""
from __future__ import annotations

from typing import Any, Callable

from . import config_writer as cw


# ---- Helpers ----------------------------------------------------------------

def _checkbox(form, name: str) -> bool:
    return form.get(name) == "1"


def _save_scalars(app_ref, edits: list[tuple[str, Any]], log) -> list[str]:
    """Apply a list of (dotted_key, value) edits. Returns error messages (empty if all ok)."""
    errors: list[str] = []
    for dotted, value in edits:
        try:
            cw.set_scalar(app_ref.cfg_path, dotted, value)
            # Mirror into the live cfg dict so the next render shows the new value.
            _mirror(app_ref.cfg, dotted.split("."), value)
        except cw.ConfigWriteError as e:
            errors.append(f"{dotted}: {e}")
        except Exception as e:
            log.warning("settings save %s failed: %s", dotted, e)
            errors.append(f"{dotted}: {e}")
    return errors


def _mirror(cfg: dict, parts: list[str], value: Any) -> None:
    """Best-effort live-cfg update so the next GET reflects the save."""
    cur = cfg
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if isinstance(cur, dict) and parts[-1] in cur:
        cur[parts[-1]] = value


# ---- Route registration -----------------------------------------------------

def register(flask_app, app_ref, SECTIONS, dcfg, maybe_reload_config: Callable, log) -> None:
    from flask import render_template, request, redirect

    theme = dcfg.get("theme", "dark")

    def _render(sub_active: str, **extra):
        return render_template(
            f"settings/{sub_active}.html",
            sections=SECTIONS, active="settings", sub_active=sub_active,
            theme=theme, flash=request.args.get("flash", ""),
            **extra,
        )

    # ---- General -----------------------------------------------------------
    @flask_app.get("/settings/general")
    def settings_general():
        cfg = app_ref.cfg
        hk = cfg.get("hotkey", {}) or {}
        wh = cfg.get("whisper", {}) or {}
        return _render("general", values={
            "hotkey_combo": hk.get("combo", ""),
            "hotkey_mode": hk.get("mode", "hold"),
            "paste_last_combo": hk.get("paste_last_combo", "") or "",
            "whisper_language": wh.get("language", "en"),
        })

    @flask_app.post("/settings/general/save")
    def settings_general_save():
        f = request.form
        combo = f.get("hotkey_combo", "").strip()
        mode = f.get("hotkey_mode", "hold").strip()
        paste = f.get("paste_last_combo", "").strip()
        lang = f.get("whisper_language", "en").strip() or "en"
        if mode not in ("hold", "toggle"):
            return redirect("/settings/general?flash=mode must be hold or toggle")
        if not combo:
            return redirect("/settings/general?flash=hotkey combo cannot be empty")
        errs = _save_scalars(app_ref, [
            ("hotkey.combo", combo),
            ("hotkey.mode", mode),
            ("hotkey.paste_last_combo", paste),
            ("whisper.language", lang),
        ], log)
        if errs:
            return redirect("/settings/general?flash=" + "; ".join(errs))
        return redirect("/settings/general?flash=Saved. Restart Echo Flow for these changes to take effect.")

    # ---- System ------------------------------------------------------------
    @flask_app.get("/settings/system")
    def settings_system():
        cfg = app_ref.cfg
        snd = cfg.get("sound", {}) or {}
        aud = cfg.get("audio", {}) or {}
        return _render("system", values={
            "sound_enabled": bool(snd.get("enabled", True)),
            "vad_enabled": bool(aud.get("vad_enabled", True)),
            "silence_timeout_ms": int(aud.get("silence_timeout_ms", 1500)),
        })

    @flask_app.post("/settings/system/save")
    def settings_system_save():
        f = request.form
        try:
            timeout = int(f.get("silence_timeout_ms", "1500"))
        except ValueError:
            return redirect("/settings/system?flash=silence_timeout_ms must be an integer")
        if not (200 <= timeout <= 10000):
            return redirect("/settings/system?flash=silence_timeout_ms must be 200..10000")
        errs = _save_scalars(app_ref, [
            ("sound.enabled", _checkbox(f, "sound_enabled")),
            ("audio.vad_enabled", _checkbox(f, "vad_enabled")),
            ("audio.silence_timeout_ms", timeout),
        ], log)
        if errs:
            return redirect("/settings/system?flash=" + "; ".join(errs))
        maybe_reload_config(app_ref)
        return redirect("/settings/system?flash=Saved.")

    # ---- Vibe --------------------------------------------------------------
    @flask_app.get("/settings/vibe")
    def settings_vibe():
        cfg = app_ref.cfg
        cu = cfg.get("cleanup", {}) or {}
        learning = (cu.get("learning") or {})
        pe = cfg.get("prompt_engineering", {}) or {}
        return _render("vibe", values={
            "cleanup_enabled": bool(cu.get("enabled", True)),
            "skip_when_clean": bool(cu.get("skip_when_clean", True)),
            "learning_enabled": bool(learning.get("enabled", True)),
            "prompt_engineering_enabled": bool(pe.get("enabled", True)),
        })

    @flask_app.post("/settings/vibe/save")
    def settings_vibe_save():
        f = request.form
        errs = _save_scalars(app_ref, [
            ("cleanup.enabled", _checkbox(f, "cleanup_enabled")),
            ("cleanup.skip_when_clean", _checkbox(f, "skip_when_clean")),
            ("cleanup.learning.enabled", _checkbox(f, "learning_enabled")),
            ("prompt_engineering.enabled", _checkbox(f, "prompt_engineering_enabled")),
        ], log)
        if errs:
            return redirect("/settings/vibe?flash=" + "; ".join(errs))
        maybe_reload_config(app_ref)
        return redirect("/settings/vibe?flash=Saved.")

    # ---- Experimental ------------------------------------------------------
    @flask_app.get("/settings/experimental")
    def settings_experimental():
        cfg = app_ref.cfg
        exp = cfg.get("experimental", {}) or {}
        return _render("experimental", values={
            "press_enter_command": bool(exp.get("press_enter_command", False)),
            "command_mode": bool(exp.get("command_mode", False)),
        })

    @flask_app.post("/settings/experimental/save")
    def settings_experimental_save():
        f = request.form
        # `experimental:` block may not exist in config.yaml on older installs;
        # we attempt the write and surface the error rather than silently
        # creating a new block (config_writer is scalar-only by design).
        errs = _save_scalars(app_ref, [
            ("experimental.press_enter_command", _checkbox(f, "press_enter_command")),
            ("experimental.command_mode", _checkbox(f, "command_mode")),
        ], log)
        if errs:
            return redirect(
                "/settings/experimental?flash="
                "Add an `experimental:` block to config.yaml with "
                "`press_enter_command: false` and `command_mode: false` to enable these toggles."
            )
        return redirect("/settings/experimental?flash=Saved.")

    # ---- Privacy -----------------------------------------------------------
    @flask_app.get("/settings/privacy")
    def settings_privacy():
        cfg = app_ref.cfg
        wh = cfg.get("whisper", {}) or {}
        cu = cfg.get("cleanup", {}) or {}
        mb = cfg.get("mobile", {}) or {}
        hist = cfg.get("history", {}) or {}
        count = 0
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                count = history.conn.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
            except Exception:
                count = 0
        status = {
            "whisper_backend": wh.get("backend", "local"),
            "cleanup_provider": cu.get("provider", "ollama"),
            "db_path": hist.get("db_path", "data/history.db"),
            "dictation_count": count,
            "mobile_enabled": bool(mb.get("enabled", False)),
        }
        return _render("privacy", status=status)

    @flask_app.post("/settings/privacy/wipe")
    def settings_privacy_wipe():
        f = request.form
        if f.get("confirm", "").strip() != "WIPE":
            return redirect('/settings/privacy?flash=Type "WIPE" in the confirm box to proceed.')
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/settings/privacy?flash=History disabled — nothing to wipe.")
        try:
            with history.conn:
                history.conn.execute("DELETE FROM dictations")
            return redirect("/settings/privacy?flash=Dictation history wiped.")
        except Exception as e:
            log.warning("privacy wipe failed: %s", e)
            return redirect(f"/settings/privacy?flash=Error: {e}")
