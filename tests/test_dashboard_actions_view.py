"""Phase 14 PR 3 — Actions top-level page + voice_actions log integration."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from src.history import History
from src.dashboard import actions_view


REPO_CFG = Path(__file__).resolve().parent.parent / "config.yaml"
HOST = {"Host": "127.0.0.1:8766"}


class _App:
    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history


def _client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dashboard"]["onboarded"] = True
    h = History(str(tmp_path / "h.db"))
    app_ref = _App(cfg, cfg_path, h)
    from src.dashboard.app import make_app
    return make_app(app_ref).test_client(), app_ref


# --- page_data() helper -----------------------------------------------------

def test_page_data_off_by_default(tmp_path):
    cfg = {"experimental": {"action_mode": False, "command_prefix": "computer",
                            "action_apps": {"spotify": "spotify"}}}
    h = History(str(tmp_path / "h.db"))
    data = actions_view.page_data(cfg, h)
    assert data["enabled"] is False
    assert data["prefix"] == "computer"
    assert data["apps"] == [
        {"name": "spotify", "target": "spotify", "source": "config", "shadows": False}
    ]
    assert isinstance(data["supported"], list) and len(data["supported"]) >= 5
    assert data["recent"] == []


def test_page_data_picks_up_recent_actions(tmp_path):
    cfg = {"experimental": {"action_mode": True, "command_prefix": "jarvis"}}
    h = History(str(tmp_path / "h.db"))
    h.log_action(body="open notepad", handler="open_app",
                 args_json='{"app": "notepad"}', label="Open notepad", ok=True)
    h.log_action(body="open frobnicate", handler="open_app",
                 args_json='{"app": "frobnicate"}', label="Open frobnicate",
                 ok=False, error="I don't have an app called frobnicate configured.")
    data = actions_view.page_data(cfg, h)
    assert data["enabled"] is True
    assert data["prefix"] == "jarvis"
    assert len(data["recent"]) == 2
    assert data["recent"][0]["ok"] is False     # newest first
    assert data["recent"][0]["ts_human"]


def test_page_data_no_history_safe(tmp_path):
    data = actions_view.page_data({"experimental": {"action_mode": True}}, None)
    assert data["recent"] == []
    assert data["intent"] is None


# --- MODEL-SHADOW: intent stats + shadow rows on the page ---------------------

def _shadow_fixture(tmp_path):
    import json
    cfg = {"experimental": {"action_mode": True, "command_prefix": "computer",
                            "action_intent_model": "shadow"}}
    h = History(str(tmp_path / "h.db"))
    # An executed regex hit the model agreed with…
    h.log_action(body="<redacted len=16>", handler="open_url",
                 args_json='{"url": "https://github.com"}',
                 label="Open https://github.com", ok=True,
                 model_pred=json.dumps(
                     {"backend": "keyword", "handler": "open_url", "conf": 0.88,
                      "gated": True, "resolved": True, "action": "open_url",
                      "agree": True, "args_match": True}))
    # …and a regex miss the model would have recovered (not executed).
    h.log_action(body="<redacted len=23>", handler="intent_shadow",
                 args_json=None, label=None, ok=True,
                 model_pred=json.dumps(
                     {"backend": "keyword", "handler": "open_url", "conf": 0.88,
                      "gated": True, "resolved": True, "action": "open_url"}))
    return cfg, h


def test_page_data_intent_stats_in_shadow_mode(tmp_path):
    cfg, h = _shadow_fixture(tmp_path)
    data = actions_view.page_data(cfg, h)
    assert data["intent"]["mode"] == "shadow"
    assert data["intent"]["hits"] == {"n": 1, "agree": 1, "args_match": 1}
    assert data["intent"]["shadow"] == {"n": 1, "resolved": 1}
    # Recent rows are enriched for the template: parsed prediction + shadow flag.
    by_handler = {r["handler"]: r for r in data["recent"]}
    shadow_row = by_handler["intent_shadow"]
    assert shadow_row["is_shadow"] is True
    assert shadow_row["model"]["action"] == "open_url"
    hit_row = by_handler["open_url"]
    assert hit_row["is_shadow"] is False
    assert hit_row["model"]["agree"] is True


def test_page_data_intent_none_when_model_off(tmp_path):
    cfg = {"experimental": {"action_mode": True}}
    h = History(str(tmp_path / "h.db"))
    assert actions_view.page_data(cfg, h)["intent"] is None


def test_page_data_tolerates_malformed_model_pred(tmp_path):
    cfg = {"experimental": {"action_mode": True, "action_intent_model": True}}
    h = History(str(tmp_path / "h.db"))
    h.log_action(body="x", handler="open_app", args_json=None,
                 model_pred="{not json")
    data = actions_view.page_data(cfg, h)
    assert data["recent"][0]["model"] is None    # parse failure → no enrichment


def test_actions_page_renders_shadow_row_and_stats(tmp_path):
    client, app_ref = _client(tmp_path)
    exp = app_ref.cfg.setdefault("experimental", {})
    exp["action_mode"] = True
    exp["action_intent_model"] = "shadow"
    import json as _json
    app_ref.history.log_action(
        body="<redacted len=23>", handler="intent_shadow", args_json=None,
        label=None, ok=True,
        model_pred=_json.dumps({"backend": "keyword", "handler": "open_url",
                                "conf": 0.88, "gated": True, "resolved": True,
                                "action": "open_url"}))
    r = client.get("/actions", headers=HOST)
    assert r.status_code == 200
    assert b">shadow<" in r.data          # the not-executed pill
    assert b"Intent model" in r.data      # the agreement summary line


# --- Route -------------------------------------------------------------------

def test_actions_page_renders(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/actions", headers=HOST)
    assert r.status_code == 200
    assert b"Supported actions" in r.data
    assert b"How to fire an action" in r.data


def test_actions_page_shows_disabled_banner_when_off(tmp_path):
    client, app_ref = _client(tmp_path)
    # Force the mode off so this tests the banner logic regardless of what the
    # shipped config.yaml currently has action_mode set to.
    app_ref.cfg.setdefault("experimental", {})["action_mode"] = False
    r = client.get("/actions", headers=HOST)
    assert b"Action Mode is off" in r.data


def test_actions_page_renders_recent_log(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.log_action(body="search the web for cats", handler="web_search",
                               args_json='{"query": "<redacted len=4>"}',
                               label="Search", ok=True)
    r = client.get("/actions", headers=HOST)
    assert b"search the web for cats" in r.data
    assert b"web_search" in r.data


def test_actions_in_sidebar(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/actions", headers=HOST)
    assert b'href="/actions"' in r.data


# --- Settings toggle ---------------------------------------------------------

def test_experimental_settings_shows_action_toggle(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/settings/experimental", headers=HOST)
    assert r.status_code == 200
    assert b'name="action_mode"' in r.data
    assert b'name="action_email_url"' in r.data


def test_experimental_save_persists_action_mode(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_mode": "1",
        "action_email_url": "https://mail.google.com",
    })
    assert r.status_code in (302, 303)
    cfg = yaml.safe_load(Path(app_ref.cfg_path).read_text(encoding="utf-8"))
    assert cfg["experimental"]["action_mode"] is True


def test_experimental_save_rejects_unsafe_email_url(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/settings/experimental/save", headers=HOST, data={
        "command_prefix": "computer",
        "action_mode": "1",
        "action_email_url": "javascript:alert(1)",
    }, follow_redirects=False)
    # Redirected back with a flash; the bad URL is not persisted.
    cfg = yaml.safe_load(Path(app_ref.cfg_path).read_text(encoding="utf-8"))
    assert cfg["experimental"]["action_email_url"] != "javascript:alert(1)"


# --- Editor: add / edit / delete app & folder shortcuts ----------------------

def test_actions_save_app_adds_shortcut(tmp_path):
    client, app_ref = _client(tmp_path)
    r = client.post("/actions/save", headers=HOST,
                    data={"kind": "app", "name": "figma", "target": "figma.exe"})
    assert r.status_code in (302, 303)
    rows = app_ref.history.list_action_targets("app")
    assert {"figma": "figma.exe"} == {x["name"]: x["target"] for x in rows}


def test_actions_save_shows_on_page_and_resolves(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "figma", "target": "figma.exe"})
    r = client.get("/actions", headers=HOST)
    assert b"figma" in r.data
    # Read-through: the voice layer sees it immediately.
    from src import voice_actions as va
    m = va.classify("open figma", app_ref.cfg)
    assert va.resolves(m, app_ref.cfg, app_ref.history) is True


def test_actions_save_rejects_bad_name(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "bad/name!", "target": "x.exe"})
    assert app_ref.history.list_action_targets("app") == []


def test_actions_save_rejects_shell_target(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "evil", "target": "calc & del *"})
    assert app_ref.history.list_action_targets("app") == []


def test_actions_delete_removes_shortcut(tmp_path):
    client, app_ref = _client(tmp_path)
    app_ref.history.set_action_target("folder", "projects", r"%USERPROFILE%\Projects")
    r = client.post("/actions/delete", headers=HOST,
                    data={"kind": "folder", "name": "projects"})
    assert r.status_code in (302, 303)
    assert app_ref.history.list_action_targets("folder") == []


def test_actions_save_edits_config_default_as_override(tmp_path):
    # Saving a name that exists in config.yaml creates a user override row.
    client, app_ref = _client(tmp_path)
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "spotify", "target": r"C:\Spotify\Spotify.exe"})
    data = actions_view.page_data(app_ref.cfg, app_ref.history)
    spotify = next(a for a in data["apps"] if a["name"] == "spotify")
    assert spotify["source"] == "user" and spotify["shadows"] is True
    assert spotify["target"].endswith("Spotify.exe")


# --- Audit fixes: save-time validation parity with launch-time guards --------

def test_actions_save_rejects_app_command_flag(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "evil", "target": "notepad /k calc"})
    assert app_ref.history.list_action_targets("app") == []


def test_actions_save_rejects_unc_folder(tmp_path):
    client, app_ref = _client(tmp_path)
    bs = chr(92)  # backslash — UNC is two leading backslashes
    unc = bs + bs + "attacker" + bs + "share"
    client.post("/actions/save", headers=HOST,
                data={"kind": "folder", "name": "share", "target": unc})
    assert app_ref.history.list_action_targets("folder") == []


def test_actions_save_url_target_ok(tmp_path):
    client, app_ref = _client(tmp_path)
    client.post("/actions/save", headers=HOST,
                data={"kind": "app", "name": "mail", "target": "https://mail.proton.me"})
    rows = {r["name"]: r["target"] for r in app_ref.history.list_action_targets("app")}
    assert rows == {"mail": "https://mail.proton.me"}
