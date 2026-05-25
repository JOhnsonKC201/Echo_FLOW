"""Dashboard Flask app factory.

Phase 0: shell only. One Blueprint per Wispr Flow IA section. Each section
currently renders a "Coming soon" placeholder so the sidebar navigation
works end-to-end. Subsequent phases fill the section blueprints in.
"""
from __future__ import annotations

from pathlib import Path

from .. import log as wlog

_log = wlog.get("dashboard.app")


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

    # --- Section routes (Phase 0 stubs) ----------------------------------
    @flask_app.get("/")
    def home():
        return render_template(
            "home.html",
            sections=SECTIONS,
            active="home",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/insights")
    def insights():
        return render_template(
            "insights.html", sections=SECTIONS, active="insights",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/dictionary")
    def dictionary():
        return render_template(
            "dictionary.html", sections=SECTIONS, active="dictionary",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/snippets")
    def snippets():
        return render_template(
            "snippets.html", sections=SECTIONS, active="snippets",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/style")
    def style():
        return render_template(
            "style.html", sections=SECTIONS, active="style",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/transforms")
    def transforms():
        return render_template(
            "transforms.html", sections=SECTIONS, active="transforms",
            theme=dcfg.get("theme", "dark"),
        )

    @flask_app.get("/scratchpad")
    def scratchpad():
        return render_template(
            "scratchpad_list.html", sections=SECTIONS, active="scratchpad",
            theme=dcfg.get("theme", "dark"),
        )

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
