"""Phase 8 — Settings panels: General / System / Vibe / Experimental / Privacy.

All saves flow through dashboard.config_writer.set_scalar so config.yaml's
comments survive. Each panel renders the current value live from app_ref.cfg
so cancelled or partial edits don't leave the UI out of sync.

The General panel surfaces a "restart required" banner since hotkey,
language, and audio device are not hot-reloadable.
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote_plus as _qp

from . import config_writer as cw
from .. import cleanup as _pe
from ..cleanup import PE_STYLES as _PE_STYLES


# ---- Helpers ----------------------------------------------------------------

def _checkbox(form, name: str) -> bool:
    return form.get(name) == "1"


def _intent_mode_str(val) -> str:
    """Config value (False / True / 'shadow') → select option string."""
    if val == "shadow":
        return "shadow"
    return "on" if val else "off"


def _intent_conf(val) -> float:
    """Config value → float, tolerating a malformed entry (mirrors the model)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.75


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
    from flask import render_template, request, redirect, jsonify

    def _render(sub_active: str, **extra):
        # Read the theme live on every render — capturing it once at register()
        # time froze the settings pages on the startup theme, so a toggle made
        # elsewhere wasn't reflected here until restart.
        return render_template(
            f"settings/{sub_active}.html",
            sections=SECTIONS, active="settings", sub_active=sub_active,
            theme=dcfg.get("theme", "dark"), flash=request.args.get("flash", ""),
            **extra,
        )

    # ---- General -----------------------------------------------------------
    @flask_app.get("/settings/general")
    def settings_general():
        cfg = app_ref.cfg
        hk = cfg.get("hotkey", {}) or {}
        wh = cfg.get("whisper", {}) or {}
        dc = cfg.get("dashboard", {}) or {}
        # language==null means auto-detect; surface it to the dropdown as "auto".
        lang_val = wh.get("language", "en")
        return _render("general", values={
            "hotkey_combo": hk.get("combo", ""),
            "hotkey_mode": hk.get("mode", "hold"),
            "paste_last_combo": hk.get("paste_last_combo", "") or "",
            "whisper_language": "auto" if lang_val in (None, "", "auto") else lang_val,
            "accent_color": dc.get("accent_color", "#3eaf6f"),
        })

    @flask_app.post("/settings/general/save")
    def settings_general_save():
        import re
        f = request.form
        combo = f.get("hotkey_combo", "").strip()
        mode = f.get("hotkey_mode", "hold").strip()
        paste = f.get("paste_last_combo", "").strip()
        lang_raw = f.get("whisper_language", "en").strip().lower()
        # "auto"/empty → null (Whisper detects per dictation); else a pinned code.
        lang: str | None = None if lang_raw in ("", "auto") else lang_raw
        accent = f.get("accent_color", "").strip()
        if mode not in ("hold", "toggle"):
            return redirect("/settings/general?flash=mode must be hold or toggle")
        if not combo:
            return redirect("/settings/general?flash=hotkey combo cannot be empty")
        # Validate accent: must be a 7-char hex (#rrggbb). Empty preserves default.
        if accent and not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
            return redirect("/settings/general?flash=accent_color must be #rrggbb")
        pairs = [
            ("hotkey.combo", combo),
            ("hotkey.mode", mode),
            ("hotkey.paste_last_combo", paste),
            ("whisper.language", lang),
        ]
        if accent:
            pairs.append(("dashboard.accent_color", accent.lower()))
        errs = _save_scalars(app_ref, pairs, log)
        if errs:
            return redirect("/settings/general?flash=" + "; ".join(errs))
        # Language is hot-applied via reload_config; hotkey/mic still need a restart.
        from .app import _maybe_reload_config
        _maybe_reload_config(app_ref)
        return redirect("/settings/general?flash=Saved. Language applies on your next dictation; restart Echo Flow for hotkey changes.")

    # ---- System ------------------------------------------------------------
    @flask_app.get("/settings/system")
    def settings_system():
        cfg = app_ref.cfg
        snd = cfg.get("sound", {}) or {}
        aud = cfg.get("audio", {}) or {}
        from .. import sound as _sound
        return _render("system", values={
            "sound_enabled": bool(snd.get("enabled", True)),
            "sound_start_alias": snd.get("start_alias", "") or "",
            "sound_stop_alias": snd.get("stop_alias", "") or "",
            "sound_error_alias": snd.get("error_alias", "") or "",
            "vad_enabled": bool(aud.get("vad_enabled", True)),
            "silence_timeout_ms": int(aud.get("silence_timeout_ms", 1500)),
        }, sound_choices=_sound.list_choices())

    @flask_app.post("/settings/system/save")
    def settings_system_save():
        f = request.form
        try:
            timeout = int(f.get("silence_timeout_ms", "1500"))
        except ValueError:
            return redirect("/settings/system?flash=silence_timeout_ms must be an integer")
        if not (200 <= timeout <= 10000):
            return redirect("/settings/system?flash=silence_timeout_ms must be 200..10000")
        edits = [
            ("sound.enabled", _checkbox(f, "sound_enabled")),
            ("audio.vad_enabled", _checkbox(f, "vad_enabled")),
            ("audio.silence_timeout_ms", timeout),
        ]
        # Persist cue aliases only when the form actually submits them, so a
        # caller posting just the toggle can't blank the user's chosen sounds.
        for field, key in (
            ("sound_start_alias", "sound.start_alias"),
            ("sound_stop_alias", "sound.stop_alias"),
            ("sound_error_alias", "sound.error_alias"),
        ):
            if field in f:
                edits.append((key, (f.get(field, "") or "").strip()))
        errs = _save_scalars(app_ref, edits, log)
        if errs:
            return redirect("/settings/system?flash=" + "; ".join(errs))
        maybe_reload_config(app_ref)
        return redirect("/settings/system?flash=Saved.")

    @flask_app.post("/settings/system/sound-preview")
    def settings_sound_preview():
        """Audition a cue so the user can pick one that's easy to notice.

        Plays server-side — the dashboard is loopback-only and runs on the same
        machine as the daemon, so the audio comes out of the user's own
        speakers. Best-effort: returns ok=false rather than 500 if the OS
        rejects the alias (e.g. non-Windows).
        """
        if request.is_json:
            alias = (request.get_json(silent=True) or {}).get("alias", "")
        else:
            alias = request.form.get("alias", "")
        try:
            from .. import sound
            ok = sound.preview(alias)
            return jsonify({"ok": bool(ok), "alias": (alias or "").strip()})
        except Exception as e:  # pragma: no cover - OS-dependent playback
            log.debug("sound preview failed: %s", e)
            return jsonify({"ok": False, "error": str(e)})

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
            "teacher_enabled": bool(learning.get("teacher_enabled", False)),
            "teacher_model": learning.get("teacher_model", "") or "",
            "trust_teacher": bool(learning.get("trust_teacher", True)),
            "prompt_engineering_enabled": bool(pe.get("enabled", True)),
            "prompt_engineering_audience": pe.get("audience", "claude-code"),
            "prompt_engineering_provider": pe.get("provider", "groq"),
            "prompt_engineering_style": _pe.normalize_pe_style(pe.get("style", "simple")),
            "prompt_engineering_styles": _PE_STYLES,
        })

    @flask_app.post("/settings/vibe/save")
    def settings_vibe_save():
        f = request.form
        _AUDIENCES = {"claude-code", "chatgpt", "generic"}
        _PROVIDERS = {"groq", "ollama", "anthropic"}
        audience = f.get("prompt_engineering_audience", "claude-code")
        if audience not in _AUDIENCES:
            audience = "claude-code"
        provider = f.get("prompt_engineering_provider", "groq")
        if provider not in _PROVIDERS:
            provider = "groq"
        pe_style = _pe.normalize_pe_style(f.get("prompt_engineering_style", "simple"))
        teacher_model = (f.get("teacher_model", "") or "").strip()
        # Keep it conservative — only Groq model names are sensible here.
        if len(teacher_model) > 80:
            teacher_model = teacher_model[:80]
        errs = _save_scalars(app_ref, [
            ("cleanup.enabled", _checkbox(f, "cleanup_enabled")),
            ("cleanup.skip_when_clean", _checkbox(f, "skip_when_clean")),
            ("cleanup.learning.enabled", _checkbox(f, "learning_enabled")),
            ("cleanup.learning.teacher_enabled", _checkbox(f, "teacher_enabled")),
            ("cleanup.learning.teacher_model", teacher_model),
            ("cleanup.learning.trust_teacher", _checkbox(f, "trust_teacher")),
            ("prompt_engineering.enabled", _checkbox(f, "prompt_engineering_enabled")),
            ("prompt_engineering.audience", audience),
            ("prompt_engineering.provider", provider),
            ("prompt_engineering.style", pe_style),
        ], log)
        if errs:
            return redirect("/settings/vibe?flash=" + "; ".join(errs))
        maybe_reload_config(app_ref)
        return redirect(f"/settings/vibe?flash=Saved — PE audience: {audience}, provider: {provider}.")

    # ---- Experimental ------------------------------------------------------
    @flask_app.get("/settings/experimental")
    def settings_experimental():
        from .. import commands as _cmds
        from .. import voice_actions as _va
        cfg = app_ref.cfg
        exp = cfg.get("experimental", {}) or {}
        return _render("experimental", values={
            "press_enter_command": bool(exp.get("press_enter_command", False)),
            "command_mode": bool(exp.get("command_mode", False)),
            "command_prefix": exp.get("command_prefix", "computer"),
            "action_mode": bool(exp.get("action_mode", False)),
            "action_email_url": exp.get("action_email_url", "https://mail.google.com"),
            "action_intent_model": _intent_mode_str(exp.get("action_intent_model", False)),
            "action_intent_min_conf": _intent_conf(exp.get("action_intent_min_conf", 0.75)),
            "humanize": _intent_mode_str(exp.get("humanize", False)),
            "humanize_use_cloud": bool(exp.get("humanize_use_cloud", False)),
            "humanize_log_verbose": bool(exp.get("humanize_log_verbose", False)),
            "humanize_min_sim": _intent_conf(exp.get("humanize_min_sim", 0.85)),
            "humanize_text_model": exp.get("humanize_text_model", "") or "",
            "humanize_text_escalate_model": exp.get(
                "humanize_text_escalate_model", "auto") or "",
            "humanize_text_min_sim": _intent_conf(
                exp.get("humanize_text_min_sim", 0.65)),
        }, supported_commands=_cmds.list_supported(),
           supported_actions=_va.list_supported(cfg))

    @flask_app.post("/settings/experimental/save")
    def settings_experimental_save():
        f = request.form
        prefix = (f.get("command_prefix", "") or "computer").strip() or "computer"
        # Guard: prefix must be alphabetic, ≥3 chars, and not a common English
        # stop word. A typo like "the" would attempt command classification on
        # nearly every dictation; unmatched commands are silently dropped, so
        # this is a data-loss risk we cheap-check on save.
        _BAD_PREFIXES = {
            "the", "and", "but", "you", "are", "all", "can", "for", "her",
            "his", "not", "now", "one", "out", "see", "two", "use", "way",
            "who", "yes", "say", "tell", "ask", "let",
        }
        if not prefix.isalpha() or len(prefix) < 3 or prefix.lower() in _BAD_PREFIXES:
            return redirect(
                "/settings/experimental?flash=command prefix must be 3+ letters "
                "and not a common English word — try 'computer' or 'jarvis'."
            )
        # `experimental:` block may not exist in config.yaml on older installs;
        # we attempt the write and surface the error rather than silently
        # creating a new block (config_writer is scalar-only by design).
        # Validate the email URL through the same guard the handler uses, so a
        # bad value can't be persisted and silently opened later.
        email_url = (f.get("action_email_url", "") or "").strip() or "https://mail.google.com"
        from .. import voice_actions as _va
        if not _va._is_safe_url(email_url):
            return redirect(
                "/settings/experimental?flash=email URL must be a safe "
                "http/https/mailto address."
            )
        # Intent-model fallback: tri-state select maps to a real YAML type
        # (bool/str), never the string "false" — a truthy "false" would read as
        # ON in main. Confidence floor is a bounded number.
        im_choice = (f.get("action_intent_model", "off") or "off").strip().lower()
        im_value = {"off": False, "on": True, "shadow": "shadow"}.get(im_choice, False)
        raw_conf = (f.get("action_intent_min_conf", "") or "").strip()
        try:
            im_conf = float(raw_conf) if raw_conf else 0.75
        except ValueError:
            return redirect(
                "/settings/experimental?flash=intent confidence must be a "
                "number between 0 and 1."
            )
        if not (0.0 <= im_conf <= 1.0):
            return redirect(
                "/settings/experimental?flash=intent confidence must be "
                "between 0 and 1."
            )
        # "My Voice" humanize: same tri-state → real YAML type mapping, plus a
        # bounded similarity floor (0–1).
        hz_choice = (f.get("humanize", "off") or "off").strip().lower()
        hz_value = {"off": False, "on": True, "shadow": "shadow"}.get(hz_choice, False)
        raw_sim = (f.get("humanize_min_sim", "") or "").strip()
        try:
            hz_sim = float(raw_sim) if raw_sim else 0.85
        except ValueError:
            return redirect(
                "/settings/experimental?flash=voice similarity floor must be a "
                "number between 0 and 1."
            )
        if not (0.0 <= hz_sim <= 1.0):
            return redirect(
                "/settings/experimental?flash=voice similarity floor must be "
                "between 0 and 1."
            )
        # The paste-in humanizer (My Voice → Humanize) carries its own model and
        # meaning floor, independent of the dictation pass above: it rewrites
        # text the user didn't write, which legitimately changes far more.
        hz_model = (f.get("humanize_text_model", "") or "").strip()
        if len(hz_model) > 100 or any(c.isspace() for c in hz_model):
            return redirect(
                "/settings/experimental?flash=humanizer model must be a single "
                "Ollama model name, e.g. qwen2.5:7b-instruct."
            )
        hz_escalate = (f.get("humanize_text_escalate_model", "") or "").strip()
        if hz_escalate and hz_escalate != "auto" and (
                len(hz_escalate) > 100 or any(c.isspace() for c in hz_escalate)):
            return redirect(
                "/settings/experimental?flash=escalation model must be 'auto', "
                "blank, or a single Ollama model name."
            )
        raw_tsim = (f.get("humanize_text_min_sim", "") or "").strip()
        try:
            hz_tsim = float(raw_tsim) if raw_tsim else 0.65
        except ValueError:
            return redirect(
                "/settings/experimental?flash=humanizer meaning floor must be a "
                "number between 0 and 1."
            )
        if not (0.0 <= hz_tsim <= 1.0):
            return redirect(
                "/settings/experimental?flash=humanizer meaning floor must be "
                "between 0 and 1."
            )
        errs = _save_scalars(app_ref, [
            ("experimental.press_enter_command", _checkbox(f, "press_enter_command")),
            ("experimental.command_mode", _checkbox(f, "command_mode")),
            ("experimental.command_prefix", prefix),
            ("experimental.action_mode", _checkbox(f, "action_mode")),
            ("experimental.action_email_url", email_url),
            ("experimental.action_intent_model", im_value),
            ("experimental.action_intent_min_conf", im_conf),
            ("experimental.humanize", hz_value),
            ("experimental.humanize_use_cloud", _checkbox(f, "humanize_use_cloud")),
            ("experimental.humanize_log_verbose", _checkbox(f, "humanize_log_verbose")),
            ("experimental.humanize_min_sim", hz_sim),
            ("experimental.humanize_text_model", hz_model),
            ("experimental.humanize_text_escalate_model", hz_escalate),
            ("experimental.humanize_text_min_sim", hz_tsim),
        ], log)
        if errs:
            return redirect(
                "/settings/experimental?flash="
                "Add an `experimental:` block to config.yaml with "
                "`press_enter_command: false` and `command_mode: false` to enable these toggles."
            )
        return redirect("/settings/experimental?flash=Saved.")

    # ---- Privacy -----------------------------------------------------------
    # PR-E: Privacy is now a top-level /privacy page. /settings/privacy
    # redirects there. The wipe POST below stays as a back-compat alias.
    @flask_app.get("/settings/privacy")
    def settings_privacy():
        return redirect("/privacy", code=302)

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
            return redirect("/settings/privacy?flash=" + _qp(f"Error: {e}"))
