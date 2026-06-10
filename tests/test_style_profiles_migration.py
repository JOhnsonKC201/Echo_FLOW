"""One-shot migration of style_profiles rows to 'polished'.

The table was seeded from an old config.yaml whose catch-all was the
minimal-touch 'default' style (never fixes grammar), and seed_from_config
never re-syncs — so the user's later config change to 'polished' silently
never took effect: 60% of dictations got 'default', and the verify/improve
loop (polished-only) stayed dormant. The migration upgrades every existing
row once; dashboard edits after that are authoritative.
"""
from __future__ import annotations

import sqlite3

from src.dashboard import style_profiles as sp


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "h.db"))
    sp.ensure_table(conn)
    return conn


def _seed_old_layout(conn):
    sp.replace_all(conn, [
        {"style": "code", "matchers": ["Code", "Cursor"]},
        {"style": "casual", "matchers": ["Slack"]},
        {"style": "default", "matchers": []},   # the stale catch-all
    ])


def test_migration_flips_all_rows_to_polished(tmp_path):
    conn = _conn(tmp_path)
    _seed_old_layout(conn)

    assert sp.migrate_profiles_to_polished(conn) is True

    styles = {p["style"] for p in sp.list_profiles(conn)}
    assert styles == {"polished"}
    # The catch-all now routes every unmatched window to grammar-fixing polish.
    assert sp.pick_style(conn, "Some Random Window") == "polished"
    assert sp.pick_style(conn, "Visual Studio Code") == "polished"


def test_migration_is_one_shot(tmp_path):
    """After the flag is set, a user who deliberately re-saves 'default' in
    the dashboard is never overridden again."""
    conn = _conn(tmp_path)
    _seed_old_layout(conn)
    assert sp.migrate_profiles_to_polished(conn) is True

    # User deliberately picks default for casual chat windows afterwards.
    sp.replace_all(conn, [
        {"style": "default", "matchers": ["Slack"]},
        {"style": "polished", "matchers": []},
    ])
    assert sp.migrate_profiles_to_polished(conn) is False
    assert sp.pick_style(conn, "Slack - #general") == "default"


def test_migration_leaves_prompt_style_alone(tmp_path):
    """'prompt' is PE mode — a different feature, not a polish level."""
    conn = _conn(tmp_path)
    sp.replace_all(conn, [
        {"style": "prompt", "matchers": ["ChatGPT"]},
        {"style": "default", "matchers": []},
    ])
    sp.migrate_profiles_to_polished(conn)
    styles = {p["style"] for p in sp.list_profiles(conn)}
    assert styles == {"prompt", "polished"}


def test_migration_on_empty_table_sets_flag_only(tmp_path):
    conn = _conn(tmp_path)
    assert sp.migrate_profiles_to_polished(conn) is False
    # Flag is set: a later seed isn't retroactively rewritten by this shot.
    assert conn.execute(
        "SELECT 1 FROM app_meta WHERE key='style_all_polished_v1'"
    ).fetchone() is not None
