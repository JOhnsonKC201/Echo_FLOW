"""Dashboard Flask app factory.

Phase 0: shell only. One Blueprint per Wispr Flow IA section. Each section
currently renders a "Coming soon" placeholder so the sidebar navigation
works end-to-end. Subsequent phases fill the section blueprints in.
"""
from __future__ import annotations

from pathlib import Path

from .. import log as wlog

_log = wlog.get("dashboard.app")


def _refresh_transform_hotkeys(app_ref) -> None:
    """Call App.refresh_transform_hotkeys() if available."""
    fn = getattr(app_ref, "refresh_transform_hotkeys", None)
    if callable(fn):
        try:
            fn()
        except Exception as e:
            _log.warning("refresh_transform_hotkeys failed: %s", e)


def _maybe_reload_config(app_ref) -> None:
    """Call App.reload_config() if it exists, swallowing errors.

    Lets dashboard mutations take effect on the next dictation without a
    full daemon restart. App.reload_config is added in Phase 3 to main.py.
    """
    fn = getattr(app_ref, "reload_config", None)
    if callable(fn):
        try:
            fn()
        except Exception as e:
            _log.warning("reload_config failed after mutation: %s", e)


# Sections in sidebar order, must match base.html nav.
SECTIONS = [
    ("home", "Home", "/", "home.html"),
    ("insights", "Insights", "/insights", "insights.html"),
    ("dictionary", "Dictionary", "/dictionary", "dictionary.html"),
    ("snippets", "Snippets", "/snippets", "snippets.html"),
    ("style", "Style", "/style", "style.html"),
    ("transforms", "Transforms", "/transforms", "transforms.html"),
    ("scratchpad", "Scratchpad", "/scratchpad", "scratchpad_list.html"),
    ("settings", "Settings", "/settings/general", "settings/general.html"),
    ("notifications", "Notifications", "/notifications", "notifications.html"),
]


# Hosts allowed in the Host: header. Defense-in-depth against DNS rebinding;
# loopback already constrains the network surface.
_ALLOWED_HOSTS: set[str] = set()


def _allowed_hosts_for(host: str, port: int) -> set[str]:
    """Build the Host: header allowlist for a given bind config."""
    return {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        f"{host}:{port}",
    }


def make_app(app_ref):
    """Build the Flask app. Imported lazily so the desktop path doesn't pay
    the Flask import cost when dashboard.enabled is false.

    app_ref is the live src.main.App so blueprints can read history, cleaner,
    config, etc.
    """
    from flask import Flask, abort, request, render_template

    here = Path(__file__).resolve().parent
    flask_app = Flask(
        __name__,
        template_folder=str(here / "templates"),
        static_folder=str(here / "static"),
        static_url_path="/static",
    )
    flask_app.config["DASHBOARD_APP_REF"] = app_ref

    # DNS-rebinding defense. Compute the allowlist from current dashboard cfg.
    dcfg = (app_ref.cfg.get("dashboard", {}) or {})
    host = dcfg.get("host", "127.0.0.1")
    port_pref = int(dcfg.get("port", 8766))
    # The allowlist must cover the actually-bound port (post-pickport); we
    # cannot know it here, so accept the preferred port plus the 4 fallbacks.
    allowlist: set[str] = set()
    for p in range(port_pref, port_pref + 5):
        allowlist |= _allowed_hosts_for(host, p)

    @flask_app.before_request
    def _enforce_host_header():
        h = request.headers.get("Host", "")
        if h not in allowlist:
            abort(400, description="bad host")

    # --- Section routes --------------------------------------------------
    @flask_app.get("/")
    def home():
        from . import analytics
        payload = {"stats": {"total_words": 0, "wpm": 0, "streak": 0}, "groups": []}
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                payload = analytics.home_payload(history.conn)
            except Exception as e:
                _log.warning("home analytics failed: %s", e)
        return render_template(
            "home.html",
            sections=SECTIONS,
            active="home",
            theme=dcfg.get("theme", "dark"),
            stats=payload["stats"],
            groups=payload["groups"],
        )

    @flask_app.get("/insights")
    def insights():
        from . import analytics
        payload = {
            "wpm": 0, "total_words": 0, "streak": 0,
            "fixes": {"words_corrected": 0, "dictionary_fixes": 0, "total": 0},
            "heatmap": {"days": [], "weeks": 14, "max": 0},
            "apps": [], "trend": [],
        }
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                payload = analytics.insights_payload(history.conn)
            except Exception as e:
                _log.warning("insights analytics failed: %s", e)
        return render_template(
            "insights.html", sections=SECTIONS, active="insights",
            theme=dcfg.get("theme", "dark"),
            **payload,
        )

    @flask_app.get("/dictionary")
    def dictionary():
        from . import vocabulary as _vocab
        from flask import request as _req
        terms = []
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                terms = _vocab.list_terms(history.conn)
            except Exception as e:
                _log.warning("dictionary list failed: %s", e)
        flash = _req.args.get("flash", "")
        return render_template(
            "dictionary.html", sections=SECTIONS, active="dictionary",
            theme=dcfg.get("theme", "dark"),
            terms=terms, flash=flash,
        )

    @flask_app.post("/dictionary/add")
    def dictionary_add():
        from . import vocabulary as _vocab
        from flask import request as _req, redirect
        term = _req.form.get("term", "").strip()
        history = getattr(app_ref, "history", None)
        msg = ""
        if not term:
            msg = "Empty term ignored."
        elif history is None or getattr(history, "conn", None) is None:
            msg = "History disabled — cannot store terms."
        else:
            try:
                _vocab.add_term(history.conn, term)
                msg = f"Added {term!r}."
                _maybe_reload_config(app_ref)
            except ValueError as e:
                msg = str(e)
            except Exception as e:
                _log.warning("dictionary add failed: %s", e)
                msg = f"Error: {e}"
        return redirect(f"/dictionary?flash={msg}")

    @flask_app.post("/dictionary/delete")
    def dictionary_delete():
        from . import vocabulary as _vocab
        from flask import request as _req, redirect
        tid = int(_req.form.get("id", "0"))
        history = getattr(app_ref, "history", None)
        msg = ""
        if history is not None and getattr(history, "conn", None) is not None and tid > 0:
            try:
                if _vocab.delete_term(history.conn, tid):
                    msg = "Term removed."
                    _maybe_reload_config(app_ref)
                else:
                    msg = "Term not found."
            except Exception as e:
                _log.warning("dictionary delete failed: %s", e)
                msg = f"Error: {e}"
        return redirect(f"/dictionary?flash={msg}")

    @flask_app.post("/dictionary/import")
    def dictionary_import():
        from . import vocabulary as _vocab
        from flask import request as _req, redirect
        raw = _req.form.get("bulk", "")
        history = getattr(app_ref, "history", None)
        msg = ""
        if history is None or getattr(history, "conn", None) is None:
            msg = "History disabled — cannot import."
        else:
            try:
                result = _vocab.bulk_import(history.conn, raw)
                msg = (f"Imported {result['added']} "
                       f"(skipped {result['duplicates']} duplicate, "
                       f"{result['invalid']} invalid).")
                _maybe_reload_config(app_ref)
            except Exception as e:
                _log.warning("dictionary import failed: %s", e)
                msg = f"Error: {e}"
        return redirect(f"/dictionary?flash={msg}")

    @flask_app.get("/snippets")
    def snippets():
        from . import snippets as _sn
        from flask import request as _req
        items = []
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                items = _sn.list_snippets(history.conn)
            except Exception as e:
                _log.warning("snippets list failed: %s", e)
        return render_template(
            "snippets.html", sections=SECTIONS, active="snippets",
            theme=dcfg.get("theme", "dark"),
            items=items, flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/snippets/add")
    def snippets_add():
        from . import snippets as _sn
        from flask import request as _req, redirect
        code = _req.form.get("code", "").strip()
        expansion = _req.form.get("expansion", "").strip()
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/snippets?flash=History disabled — cannot save.")
        try:
            _sn.add_snippet(history.conn, code, expansion)
            _maybe_reload_config(app_ref)
            return redirect(f"/snippets?flash=Saved {code!r}.")
        except ValueError as e:
            return redirect(f"/snippets?flash={e}")

    @flask_app.post("/snippets/delete")
    def snippets_delete():
        from . import snippets as _sn
        from flask import request as _req, redirect
        sid = int(_req.form.get("id", "0"))
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None or sid <= 0:
            return redirect("/snippets?flash=Nothing to remove.")
        _sn.delete_snippet(history.conn, sid)
        _maybe_reload_config(app_ref)
        return redirect("/snippets?flash=Snippet removed.")

    @flask_app.post("/snippets/import")
    def snippets_import():
        from . import snippets as _sn
        from flask import request as _req, redirect
        raw = _req.form.get("bulk", "")
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/snippets?flash=History disabled — cannot import.")
        r = _sn.bulk_import(history.conn, raw)
        _maybe_reload_config(app_ref)
        msg = (f"Imported {r['added']} new, updated {r['updated']}, "
               f"skipped {r['invalid']} malformed.")
        return redirect(f"/snippets?flash={msg}")

    @flask_app.get("/style")
    def style():
        from . import style_profiles as _sp
        from flask import request as _req
        profiles = []
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                profiles = _sp.list_profiles(history.conn)
                if not profiles:
                    # Seed from config defaults so the table isn't blank on first open.
                    cleanup_cfg = app_ref.cfg.get("cleanup", {}) or {}
                    _sp.seed_from_config(history.conn, cleanup_cfg.get("profiles") or [])
                    profiles = _sp.list_profiles(history.conn)
            except Exception as e:
                _log.warning("style list failed: %s", e)
        return render_template(
            "style.html", sections=SECTIONS, active="style",
            theme=dcfg.get("theme", "dark"),
            profiles=profiles,
            valid_styles=("default", "code", "casual", "email", "prompt"),
            flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/style/save")
    def style_save():
        from . import style_profiles as _sp
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/style?flash=History disabled — cannot save.")
        # Form: parallel arrays style[] and matchers[] (one matcher line per profile).
        styles = _req.form.getlist("style")
        matchers_raw = _req.form.getlist("matchers")
        new_profiles = []
        for s, m in zip(styles, matchers_raw):
            matchers = [piece.strip() for piece in m.replace(",", "\n").splitlines() if piece.strip()]
            new_profiles.append({"style": s, "matchers": matchers})
        try:
            _sp.replace_all(history.conn, new_profiles)
            _maybe_reload_config(app_ref)
            return redirect("/style?flash=Profiles saved.")
        except ValueError as e:
            return redirect(f"/style?flash={e}")

    @flask_app.get("/transforms")
    def transforms():
        from . import transforms as _tf
        from flask import request as _req
        items = []
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                # Seed builtins if table is empty.
                if not _tf.list_transforms(history.conn):
                    _tf.seed_builtins(history.conn)
                items = _tf.list_transforms(history.conn)
            except Exception as e:
                _log.warning("transforms list failed: %s", e)
        return render_template(
            "transforms.html", sections=SECTIONS, active="transforms",
            theme=dcfg.get("theme", "dark"),
            items=items, flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/transforms/add")
    def transforms_add():
        from . import transforms as _tf
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/transforms?flash=History disabled — cannot save.")
        name = _req.form.get("name", "").strip()
        prompt = _req.form.get("system_prompt", "").strip()
        hotkey = _req.form.get("hotkey", "").strip() or None
        try:
            _tf.add_transform(history.conn, name=name, system_prompt=prompt, hotkey=hotkey)
            _refresh_transform_hotkeys(app_ref)
            return redirect(f"/transforms?flash=Added {name!r}.")
        except ValueError as e:
            return redirect(f"/transforms?flash={e}")

    @flask_app.post("/transforms/delete")
    def transforms_delete():
        from . import transforms as _tf
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/transforms?flash=History disabled.")
        tid = int(_req.form.get("id", "0"))
        try:
            if _tf.delete_transform(history.conn, tid):
                _refresh_transform_hotkeys(app_ref)
                return redirect("/transforms?flash=Removed.")
            return redirect("/transforms?flash=Not found.")
        except ValueError as e:
            return redirect(f"/transforms?flash={e}")

    @flask_app.post("/transforms/bind-hotkey")
    def transforms_bind_hotkey():
        from . import transforms as _tf
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/transforms?flash=History disabled.")
        tid = int(_req.form.get("id", "0"))
        combo = _req.form.get("hotkey", "").strip() or None
        try:
            _tf.update_transform(history.conn, tid, hotkey=combo)
            _refresh_transform_hotkeys(app_ref)
            return redirect("/transforms?flash=Hotkey updated.")
        except ValueError as e:
            return redirect(f"/transforms?flash={e}")

    @flask_app.post("/transforms/toggle")
    def transforms_toggle():
        from . import transforms as _tf
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/transforms?flash=History disabled.")
        tid = int(_req.form.get("id", "0"))
        enabled = _req.form.get("enabled") == "1"
        try:
            _tf.update_transform(history.conn, tid, enabled=enabled)
            _refresh_transform_hotkeys(app_ref)
            return redirect("/transforms?flash=Updated.")
        except ValueError as e:
            return redirect(f"/transforms?flash={e}")

    @flask_app.get("/scratchpad")
    def scratchpad():
        from . import scratchpad as _sp
        from flask import request as _req
        items = []
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                items = _sp.list_scratchpads(history.conn)
            except Exception as e:
                _log.warning("scratchpad list failed: %s", e)
        target_id = getattr(app_ref, "_scratchpad_target_id", None)
        return render_template(
            "scratchpad_list.html", sections=SECTIONS, active="scratchpad",
            theme=dcfg.get("theme", "dark"),
            items=items, flash=_req.args.get("flash", ""),
            target_id=target_id,
        )

    @flask_app.get("/scratchpad/<int:pad_id>")
    def scratchpad_edit(pad_id: int):
        from . import scratchpad as _sp
        from flask import abort, request as _req
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            abort(404)
        pad = _sp.get_scratchpad(history.conn, pad_id)
        if pad is None:
            abort(404)
        target_id = getattr(app_ref, "_scratchpad_target_id", None)
        return render_template(
            "scratchpad_edit.html", sections=SECTIONS, active="scratchpad",
            theme=dcfg.get("theme", "dark"),
            pad=pad, flash=_req.args.get("flash", ""),
            is_target=(target_id == pad_id),
        )

    @flask_app.post("/scratchpad/new")
    def scratchpad_new():
        from . import scratchpad as _sp
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/scratchpad?flash=History disabled.")
        title = _req.form.get("title", "").strip()
        pad_id = _sp.create_scratchpad(history.conn, title=title or "Untitled")
        return redirect(f"/scratchpad/{pad_id}")

    @flask_app.post("/scratchpad/save")
    def scratchpad_save():
        from . import scratchpad as _sp
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/scratchpad?flash=History disabled.")
        pid = int(_req.form.get("id", "0"))
        title = _req.form.get("title", "")
        body = _req.form.get("body", "")
        if _sp.save_scratchpad(history.conn, pid, title=title, body=body):
            return redirect(f"/scratchpad/{pid}?flash=Saved.")
        return redirect("/scratchpad?flash=Not found.")

    @flask_app.post("/scratchpad/delete")
    def scratchpad_delete():
        from . import scratchpad as _sp
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/scratchpad?flash=History disabled.")
        pid = int(_req.form.get("id", "0"))
        # If we just deleted the target, clear the arming.
        if getattr(app_ref, "_scratchpad_target_id", None) == pid:
            try:
                app_ref._scratchpad_target_id = None
            except Exception:
                pass
        if _sp.delete_scratchpad(history.conn, pid):
            return redirect("/scratchpad?flash=Deleted.")
        return redirect("/scratchpad?flash=Not found.")

    @flask_app.post("/scratchpad/target")
    def scratchpad_target():
        """Arm a scratchpad to receive the next dictation (toggle: same id clears)."""
        from flask import request as _req, redirect
        pid_raw = _req.form.get("id", "0")
        try:
            pid = int(pid_raw)
        except ValueError:
            pid = 0
        try:
            current = getattr(app_ref, "_scratchpad_target_id", None)
            if pid <= 0 or current == pid:
                app_ref._scratchpad_target_id = None
                msg = "Stopped dictating into scratchpad."
            else:
                app_ref._scratchpad_target_id = pid
                msg = f"Next dictations will append to scratchpad #{pid}."
        except Exception as e:
            msg = f"Error: {e}"
        back = _req.form.get("back", "/scratchpad")
        return redirect(f"{back}?flash={msg}")

    @flask_app.get("/settings/general")
    def settings_general():
        return render_template(
            "settings/general.html", sections=SECTIONS, active="settings",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/notifications")
    def notifications():
        return render_template(
            "notifications.html", sections=SECTIONS, active="notifications",
            theme=dcfg.get("theme", "dark"),
        )

    # --- Health / API ----------------------------------------------------
    @flask_app.get("/api/healthz")
    def healthz():
        from flask import jsonify
        return jsonify({"ok": True})

    return flask_app
