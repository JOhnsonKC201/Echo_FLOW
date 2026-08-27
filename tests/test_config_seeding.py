"""The shipped config must be the local-first one, on every install path.

`config.yaml` at the repo root used to be tracked AND read directly by a source
checkout, so a clone inherited whatever the maintainer had switched on. v0.3.1
fixed that for the installers by bundling `packaging/default/config.yaml`, but
the seeding in main.load_config was gated on `sys.frozen`, so the from-source
path (the one the README hands developers) still shipped
`cleanup.provider: groq` + `allow_cloud_cleanup: true` while the README two
sections above promised "Regular dictation is 100% local".

The working config is gitignored now and the factory default is the only config
in git, so there is exactly one answer to "what does a new user start with".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import src.main as main

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "packaging" / "default" / "config.yaml"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", *args), cwd=str(REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


requires_git = pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0,
    reason="not a git checkout (source tarball or export)",
)


# --- what git carries ------------------------------------------------------

@requires_git
def test_working_config_is_not_tracked_in_git():
    """The regression itself. If this file is ever committed again, every
    cloner inherits the committer's cloud settings."""
    r = _git("ls-files", "--error-unmatch", "config.yaml")
    assert r.returncode != 0, (
        "config.yaml is tracked again. It is a working config: whatever is "
        "switched on in it becomes every cloner's default. Run "
        "`git rm --cached config.yaml` and put the change in "
        "packaging/default/config.yaml instead."
    )


@requires_git
def test_the_factory_default_is_tracked():
    assert _git("ls-files", "--error-unmatch",
                "packaging/default/config.yaml").returncode == 0


@requires_git
def test_gitignore_covers_the_working_config_only():
    assert _git("check-ignore", "-q", "config.yaml").returncode == 0, \
        "config.yaml must be ignored so a local edit cannot be committed"
    assert _git("check-ignore", "-q",
                "packaging/default/config.yaml").returncode != 0, \
        "the factory default must stay tracked; it is what ships"


# --- what a first run produces ---------------------------------------------

def test_source_mode_seeds_from_the_tracked_template():
    """Not frozen under pytest, so this is the from-source branch: the one
    that was broken."""
    assert not getattr(sys, "frozen", False)
    assert main.FACTORY_CONFIG == TEMPLATE
    assert TEMPLATE.exists()


def test_first_run_writes_a_config_and_it_is_local_first(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(main, "FACTORY_CONFIG", TEMPLATE)
    assert not cfg_path.exists()

    cfg = main.load_config()

    assert cfg_path.exists(), "first run must seed a config"
    assert cfg["cleanup"]["provider"] == "ollama"
    assert cfg["cleanup"]["allow_cloud_cleanup"] is False
    assert cfg["cleanup"]["verify"]["escalate_cloud"] is False
    assert cfg["cleanup"]["learning"]["teacher_enabled"] is False


def test_first_run_never_overwrites_an_existing_config(tmp_path, monkeypatch):
    """Seeding is first-run only; an upgrade must not reset your settings."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("history:\n  db_path: mine.db\n", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(main, "FACTORY_CONFIG", TEMPLATE)

    cfg = main.load_config()

    assert cfg == {"history": {"db_path": "mine.db"}}


def test_seeded_config_is_byte_identical_to_the_template(tmp_path, monkeypatch):
    """Comments included: the file a user opens is the file we reviewed."""
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(main, "FACTORY_CONFIG", TEMPLATE)
    main.load_config()
    assert cfg_path.read_text(encoding="utf-8") == \
        TEMPLATE.read_text(encoding="utf-8")


def test_a_missing_template_does_not_crash_the_seed_path(tmp_path, monkeypatch):
    """A broken bundle should fail on the missing config, not on the copy."""
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(main, "FACTORY_CONFIG", tmp_path / "nope.yaml")
    with pytest.raises(FileNotFoundError):
        main.load_config()


# --- the guard that runs where the byte-compare cannot ---------------------

def test_factory_check_passes_without_a_working_config(tmp_path):
    """CI has no config.yaml, so make_default_config --check falls back to
    verifying the tracked default's values. That fallback must actually run
    and pass, or the safety net is silently absent in the one place it is the
    only net there is."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_mdc", REPO / "scripts" / "make_default_config.py")
    mdc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mdc)
    mdc.SOURCE = tmp_path / "absent.yaml"
    assert mdc.check_factory_values() == 0


def test_factory_check_rejects_a_cloud_default(tmp_path):
    """And it must fail when the values are wrong, or it proves nothing."""
    import importlib.util
    from src.dashboard.config_writer import set_scalar
    spec = importlib.util.spec_from_file_location(
        "_mdc", REPO / "scripts" / "make_default_config.py")
    mdc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mdc)

    bad = REPO / ".tmp_factory_check.yaml"   # inside REPO: relative_to() needs it
    try:
        shutil.copy(TEMPLATE, bad)
        set_scalar(bad, "cleanup.provider", "groq")
        set_scalar(bad, "cleanup.allow_cloud_cleanup", True)
        mdc.SOURCE = tmp_path / "absent.yaml"
        mdc.TARGET = bad
        assert mdc.check_factory_values() == 1
    finally:
        bad.unlink(missing_ok=True)


def test_template_parses_and_is_not_a_stub():
    cfg = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(cfg, dict)
    for section in ("hotkey", "audio", "whisper", "cleanup", "dashboard"):
        assert section in cfg, f"factory default lost its {section} section"
