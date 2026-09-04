"""PATH setup for the CTranslate2 CUDA libraries.

CTranslate2 fixes its DLL search path when its extension is imported, so the
NVIDIA wheel directories have to be on PATH *before* that happens, and
``os.add_dll_directory`` is not a substitute.
"""
from __future__ import annotations

import os
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """ensure() memoises; each test needs a clean slate and an intact PATH."""
    import src.cuda_dlls as cd
    monkeypatch.setattr(cd, "_cached", None)
    monkeypatch.setenv("PATH", "C:/pre-existing")
    yield


def _fake_nvidia(monkeypatch, tmp_path, libs=("cublas", "cudnn")):
    for lib in libs:
        (tmp_path / lib / "bin").mkdir(parents=True)
    mod = types.ModuleType("nvidia")
    mod.__path__ = [str(tmp_path)]      # namespace package: __path__, no __file__
    monkeypatch.setitem(sys.modules, "nvidia", mod)


@pytest.mark.skipif(os.name != "nt", reason="PATH-based DLL lookup is Windows-only")
def test_adds_wheel_bin_dirs_to_front_of_path(monkeypatch, tmp_path):
    import src.cuda_dlls as cd
    _fake_nvidia(monkeypatch, tmp_path)

    added = cd.ensure()

    assert len(added) == 2
    assert all(d.endswith("bin") for d in added)
    # Prepended, and the caller's PATH is preserved.
    assert os.environ["PATH"].startswith(added[0])
    assert os.environ["PATH"].endswith("C:/pre-existing")


@pytest.mark.skipif(os.name != "nt", reason="PATH-based DLL lookup is Windows-only")
def test_is_idempotent(monkeypatch, tmp_path):
    """Called per Transcriber build; must not grow PATH without bound."""
    import src.cuda_dlls as cd
    _fake_nvidia(monkeypatch, tmp_path)

    first = cd.ensure()
    path_after_first = os.environ["PATH"]
    second = cd.ensure()

    assert first == second
    assert os.environ["PATH"] == path_after_first


def test_no_wheels_installed_is_a_silent_no_op(monkeypatch):
    """The CPU-only install is the default; it must not warn or raise."""
    import src.cuda_dlls as cd
    monkeypatch.setitem(sys.modules, "nvidia", None)   # import raises

    assert cd.ensure() == []
    assert os.environ["PATH"] == "C:/pre-existing"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only branch")
def test_ignores_wheel_dirs_without_a_bin_folder(monkeypatch, tmp_path):
    import src.cuda_dlls as cd
    (tmp_path / "cublas" / "bin").mkdir(parents=True)
    (tmp_path / "cuda_runtime" / "include").mkdir(parents=True)   # no bin/
    mod = types.ModuleType("nvidia")
    mod.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, "nvidia", mod)

    added = cd.ensure()

    assert len(added) == 1
    assert "cublas" in added[0]
