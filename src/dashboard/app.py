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
# PR-E reorder: Commands promoted out of /settings/experimental to top-level
# (between Transforms and Scratchpad); Privacy promoted out of /settings to
# top-level (between Scratchpad and Settings).
SECTIONS = [
    ("home", "Home", "/", "home.html"),
    ("insights", "Outcomes", "/insights", "insights.html"),
    ("graph", "Graph", "/graph", "graph.html"),
    ("dictionary", "Dictionary", "/dictionary", "dictionary.html"),
    ("snippets", "Snippets", "/snippets", "snippets.html"),
    ("style", "Style", "/style", "style.html"),
    ("transforms", "Transforms", "/transforms", "transforms.html"),
    ("commands", "Commands", "/commands", "commands.html"),
    ("scratchpad", "Scratchpad", "/scratchpad", "scratchpad_list.html"),
    ("privacy", "Privacy", "/privacy", "privacy.html"),
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
        from . import analytics, inbox
        from flask import redirect, request as _req
        # First-run onboarding gate. Default True so existing installs (no
        # onboarded key in config) skip the tour.
        if not dcfg.get("onboarded", True):
            return redirect("/onboarding")
        items: list = []
        today = {"count": 0, "time_saved_ms": 0,
                 "acceptance": {"current": 0.0, "delta_pp": 0.0, "n_current": 0},
                 "latency": {"p50": None, "p95": None, "n": 0}}
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                rows = inbox.inbox_rows(history.conn, n=15)
                for r in rows:
                    r["ts_human"] = inbox.format_ts(r["ts"])
                    r["has_diff"] = inbox.has_diff(r["raw_text"], r["cleaned_text"])
                    r["diff"] = inbox.render_diff(r["raw_text"], r["cleaned_text"]) if r["has_diff"] else []
                    items.append(r)
                today = analytics.today_summary(history.conn)
            except Exception as e:
                _log.warning("home inbox failed: %s", e)
        acc_pct = int(round((today["acceptance"]["current"] or 0) * 100))
        return render_template(
            "home.html",
            sections=SECTIONS,
            active="home",
            theme=dcfg.get("theme", "dark"),
            items=items,
            today=today,
            time_saved_today=analytics.humanize_ms(today["time_saved_ms"]),
            acceptance_pct=acc_pct,
            flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/inbox/rate")
    def inbox_rate():
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/?flash=History disabled.")
        try:
            did = int(_req.form.get("id", "0"))
        except ValueError:
            did = 0
        raw_rating = (_req.form.get("rating", "") or "").strip()
        rating: int | None
        if raw_rating == "":
            rating = None
        else:
            try:
                rating = int(raw_rating)
            except ValueError:
                return redirect("/?flash=Bad rating value.")
            if rating not in (1, -1):
                return redirect("/?flash=Rating must be 1, -1, or empty.")
        if did <= 0:
            return redirect("/?flash=Bad dictation id.")
        try:
            history.rate_dictation(did, rating)
        except Exception as e:
            _log.warning("rate_dictation failed: %s", e)
            return redirect(f"/?flash=Error: {e}")
        # Anchor jump back to the rated card.
        return redirect(f"/#d-{did}")

    @flask_app.get("/inbox/<int:did>/edit")
    def inbox_edit_view(did: int):
        from flask import abort, request as _req
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            abort(404)
        row = history.conn.execute(
            "SELECT id, ts, window_title, style, raw_text, cleaned_text, "
            "       original_cleaned, source FROM dictations WHERE id = ?",
            (did,),
        ).fetchone()
        if row is None:
            abort(404)
        return render_template(
            "inbox_edit.html",
            sections=SECTIONS, active="home",
            theme=dcfg.get("theme", "dark"),
            row={
                "id": row[0], "ts": row[1], "window_title": row[2] or "",
                "style": row[3] or "default", "raw_text": row[4] or "",
                "cleaned_text": row[5] or "", "original_cleaned": row[6] or "",
                "source": row[7] or "desktop",
            },
            flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/inbox/<int:did>/edit")
    def inbox_edit_save(did: int):
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/?flash=History disabled.")
        new_cleaned = _req.form.get("cleaned_text", "")
        try:
            with history.conn:
                history.conn.execute(
                    "UPDATE dictations SET cleaned_text = ? WHERE id = ?",
                    (new_cleaned, did),
                )
        except Exception as e:
            _log.warning("inbox edit save failed: %s", e)
            return redirect(f"/inbox/{did}/edit?flash=Error: {e}")
        return redirect(f"/#d-{did}")

    @flask_app.get("/insights")
    def insights():
        from . import analytics
        payload = {
            "wpm": 0, "total_words": 0, "streak": 0,
            "fixes": {"words_corrected": 0, "dictionary_fixes": 0, "total": 0},
            "heatmap": {"days": [], "weeks": 14, "max": 0},
            "apps": [], "trend": [],
        }
        outcomes = {
            "time_saved_ms": 0,
            "acceptance": {"current": 0.0, "prior": 0.0, "delta_pp": 0.0,
                           "n_current": 0, "n_prior": 0},
            "latency": {"p50": None, "p95": None, "n": 0},
        }
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                payload = analytics.insights_payload(history.conn)
                outcomes["time_saved_ms"] = analytics.time_saved_ms(history.conn, days=30)
                outcomes["acceptance"] = analytics.acceptance_rate(history.conn, days=7)
                outcomes["latency"] = analytics.latency_percentiles(history.conn, n=200)
            except Exception as e:
                _log.warning("insights analytics failed: %s", e)
        acc_pct = int(round((outcomes["acceptance"]["current"] or 0) * 100))
        return render_template(
            "insights.html", sections=SECTIONS, active="insights",
            theme=dcfg.get("theme", "dark"),
            time_saved_human=analytics.humanize_ms(outcomes["time_saved_ms"]),
            baseline_wpm=40,
            acceptance=outcomes["acceptance"],
            acceptance_pct=acc_pct,
            latency=outcomes["latency"],
            trend=payload["trend"],
            apps=payload["apps"],
            fixes=payload["fixes"],
            wpm=payload["wpm"],
            total_words=payload["total_words"],
            streak=payload["streak"],
            heatmap=payload["heatmap"],
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
        from . import bulk_import as _bulk
        from flask import request as _req, redirect
        paste = _req.form.get("bulk", "")
        upload = _bulk.read_upload(_req.files.get("file"))
        raw = _bulk.merge_text(paste, upload)
        history = getattr(app_ref, "history", None)
        msg = ""
        if history is None or getattr(history, "conn", None) is None:
            msg = "History disabled — cannot import."
        elif not raw.strip():
            msg = "Nothing to import — paste or upload some terms first."
            return redirect(f"/dictionary?flash={msg}")
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
        from . import bulk_import as _bulk
        from flask import request as _req, redirect
        paste = _req.form.get("bulk", "")
        upload = _bulk.read_upload(_req.files.get("file"))
        raw = _bulk.merge_text(paste, upload)
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/snippets?flash=History disabled — cannot import.")
        if not raw.strip():
            return redirect("/snippets?flash=Nothing to import — paste or upload some snippets first.")
        # CSV files use `code,expansion` per line; coerce to the parser's
        # native format. Mixed input (some `=`, some `,`) is also supported.
        raw = _bulk.csv_to_snippet_lines(raw)
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

    # --- Commands (PR-E top-level promotion of Phase 13) ----------------
    @flask_app.get("/commands")
    def commands_page():
        from . import commands_view as _cv
        from flask import request as _req
        history = getattr(app_ref, "history", None)
        data = _cv.page_data(app_ref.cfg, history)
        return render_template(
            "commands.html", sections=SECTIONS, active="commands",
            theme=dcfg.get("theme", "dark"),
            data=data, flash=_req.args.get("flash", ""),
        )

    # --- Privacy (PR-E first-class promotion) ---------------------------
    @flask_app.get("/privacy")
    def privacy_page():
        from . import privacy as _priv
        from flask import request as _req
        cfg_path = Path(getattr(app_ref, "cfg_path", "config.yaml"))
        history = getattr(app_ref, "history", None)
        db_path_str = (app_ref.cfg.get("history", {}) or {}).get("db_path", "data/history.db")
        db_path = (cfg_path.parent / db_path_str).resolve() if not Path(db_path_str).is_absolute() else Path(db_path_str)
        data_dir = (cfg_path.parent / "data").resolve()
        ledger = _priv.ledger(app_ref.cfg, db_path, cfg_path, data_dir)
        last_cfg_human = _priv.human_age(ledger["last_config_write"])
        return render_template(
            "privacy.html", sections=SECTIONS, active="privacy",
            theme=dcfg.get("theme", "dark"),
            ledger=ledger,
            db_size_human=_priv.humanize_bytes(ledger["db_size_bytes"]),
            audio_size_human=_priv.humanize_bytes(ledger["audio_size_bytes"]),
            last_cfg_human=last_cfg_human,
            flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/privacy/wipe")
    def privacy_wipe():
        from flask import request as _req, redirect
        if (_req.form.get("confirm", "") or "").strip() != "WIPE":
            return redirect('/privacy?flash=Type "WIPE" in the confirm box to proceed.')
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/privacy?flash=History disabled — nothing to wipe.")
        try:
            with history.conn:
                history.conn.execute("DELETE FROM dictations")
            return redirect("/privacy?flash=Dictation history wiped.")
        except Exception as e:
            _log.warning("privacy wipe failed: %s", e)
            return redirect(f"/privacy?flash=Error: {e}")

    @flask_app.get("/privacy/export.zip")
    def privacy_export():
        from . import privacy as _priv
        from flask import send_file
        import io
        cfg_path = Path(getattr(app_ref, "cfg_path", "config.yaml"))
        db_path_str = (app_ref.cfg.get("history", {}) or {}).get("db_path", "data/history.db")
        db_path = (cfg_path.parent / db_path_str).resolve() if not Path(db_path_str).is_absolute() else Path(db_path_str)
        data = _priv.build_export_zip(cfg_path, db_path)
        return send_file(
            io.BytesIO(data), mimetype="application/zip",
            as_attachment=True, download_name="echo-flow-export.zip",
        )

    @flask_app.post("/privacy/open-folder")
    def privacy_open_folder():
        from flask import redirect
        import subprocess, sys
        cfg_path = Path(getattr(app_ref, "cfg_path", "config.yaml"))
        data_dir = (cfg_path.parent / "data").resolve()
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(data_dir)], close_fds=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(data_dir)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(data_dir)], close_fds=True)
            return redirect("/privacy?flash=Opening data folder…")
        except Exception as e:
            _log.warning("open data folder failed: %s", e)
            return redirect(f"/privacy?flash=Could not open: {e}")

    # --- Settings (Phase 8) ---------------------------------------------
    from . import settings_routes as _settings
    _settings.register(flask_app, app_ref, SECTIONS, dcfg, _maybe_reload_config, _log)

    @flask_app.get("/notifications")
    def notifications():
        from . import notifications as _nf
        from flask import request as _req
        import datetime as _dt
        items = []
        history = getattr(app_ref, "history", None)
        if history is not None and getattr(history, "conn", None) is not None:
            try:
                raw = _nf.list_recent(history.conn)
                for r in raw:
                    ts = r.get("ts") or 0
                    try:
                        r["ts_human"] = _dt.datetime.fromtimestamp(ts).strftime("%b %d, %H:%M")
                    except Exception:
                        r["ts_human"] = ""
                    items.append(r)
            except Exception as e:
                _log.warning("notifications list failed: %s", e)
        return render_template(
            "notifications.html", sections=SECTIONS, active="notifications",
            theme=dcfg.get("theme", "dark"),
            items=items, flash=_req.args.get("flash", ""),
        )

    @flask_app.post("/notifications/mark-read")
    def notifications_mark_read():
        from . import notifications as _nf
        from flask import request as _req, redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/notifications?flash=History disabled.")
        try:
            nid = int(_req.form.get("id", "0"))
        except ValueError:
            nid = 0
        _nf.mark_read(history.conn, nid)
        return redirect("/notifications")

    @flask_app.post("/notifications/mark-all-read")
    def notifications_mark_all_read():
        from . import notifications as _nf
        from flask import redirect
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return redirect("/notifications?flash=History disabled.")
        n = _nf.mark_all_read(history.conn)
        return redirect(f"/notifications?flash=Marked {n} read.")

    @flask_app.get("/api/notifications/unread.json")
    def notifications_unread_json():
        from . import notifications as _nf
        from flask import jsonify
        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return jsonify({"unread": 0})
        try:
            return jsonify({"unread": _nf.unread_count(history.conn)})
        except Exception as e:
            _log.warning("unread count failed: %s", e)
            return jsonify({"unread": 0})

    # --- Theme toggle (Phase 11) ----------------------------------------
    @flask_app.post("/api/theme")
    def api_theme():
        """Flip dashboard.theme between dark and light. Persists via config_writer."""
        from . import config_writer as _cw
        from flask import request as _req, jsonify
        want = (_req.form.get("theme") or _req.args.get("theme") or "").strip()
        if want not in ("dark", "light"):
            cur = dcfg.get("theme", "dark")
            want = "light" if cur == "dark" else "dark"
        try:
            _cw.set_scalar(app_ref.cfg_path, "dashboard.theme", want)
            dcfg["theme"] = want  # mirror so subsequent renders pick it up
            return jsonify({"ok": True, "theme": want})
        except Exception as e:
            _log.warning("theme toggle failed: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- Onboarding (Phase 11) ------------------------------------------
    @flask_app.get("/onboarding")
    def onboarding():
        return render_template(
            "onboarding.html", sections=SECTIONS, active="home",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.post("/onboarding/finish")
    def onboarding_finish():
        from . import config_writer as _cw
        from flask import redirect
        # Always flip the in-process flag so the user is never stuck in a
        # redirect loop, even if the disk write fails (read-only fs, missing
        # key, etc). The persistence failure surfaces as a notification toast
        # next restart will re-show the tour, which is the lesser evil.
        dcfg["onboarded"] = True
        try:
            _cw.set_scalar(app_ref.cfg_path, "dashboard.onboarded", True)
        except Exception as e:
            _log.warning("onboarding finish persistence failed: %s", e)
        return redirect("/")

    # --- Health / API ----------------------------------------------------
    @flask_app.get("/api/healthz")
    def healthz():
        from flask import jsonify
        return jsonify({"ok": True})

    # --- Knowledge graph -------------------------------------------------
    # /graph     — dashboard-wrapped view (iframe shell, preserves sidebar)
    # /graph/raw — self-contained D3 HTML produced by graph_obsidian.render
    # Cached by db mtime; lock prevents duplicate expensive renders under
    # concurrent first-page-loads on the threaded Werkzeug server.
    import threading as _threading
    _graph_cache: dict = {"mtime": None, "html": None}
    _graph_cache_lock = _threading.Lock()

    @flask_app.get("/graph")
    def graph_view():
        return render_template(
            "graph.html",
            sections=SECTIONS,
            active="graph",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/graph/raw")
    def graph_raw():
        from flask import Response, request as _req
        from . import graph_obsidian

        history = getattr(app_ref, "history", None)
        if history is None or getattr(history, "conn", None) is None:
            return Response("<h1>History disabled</h1>", mimetype="text/html")

        db_path = str((Path.cwd() / app_ref.cfg.get("history", {})
                       .get("db_path", "data/history.db")).resolve())
        try:
            mtime = Path(db_path).stat().st_mtime
        except OSError:
            mtime = None

        force = _req.args.get("refresh") == "1"
        with _graph_cache_lock:
            if not force and _graph_cache["html"] and _graph_cache["mtime"] == mtime:
                return Response(_graph_cache["html"], mimetype="text/html")
            try:
                html = graph_obsidian.render(db_path)
            except Exception as e:
                _log.warning("graph render failed: %s", e)
                return Response(f"<h1>Graph render failed</h1><pre>{e}</pre>",
                                mimetype="text/html")
            _graph_cache["mtime"] = mtime
            _graph_cache["html"] = html
        return Response(html, mimetype="text/html")

    return flask_app
