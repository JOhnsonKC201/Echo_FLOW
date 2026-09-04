"""Device selection runs off CTranslate2, not torch, and never breaks dictation.

The bug this pins: faster-whisper runs on CTranslate2, but the device check used
``torch.cuda.is_available()``. The stock CPU-only torch wheel reports no CUDA,
so Whisper was pinned to the CPU on a machine with a perfectly usable GPU.
"""
from __future__ import annotations

import sys
import types

import pytest


class _FakeModel:
    """Stands in for WhisperModel. ``ok`` decides whether inference works."""

    def __init__(self, name, device=None, compute_type=None, ok=True):
        self.name = name
        self.device = device
        self.compute_type = compute_type
        self._ok = ok
        self.calls: list[dict] = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if not self._ok:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return iter(()), types.SimpleNamespace(language="en")


def _install_fake_whisper(monkeypatch, *, gpu_works=True, built=None):
    """Make ``from faster_whisper import WhisperModel`` yield a fake."""
    def _factory(name, device=None, compute_type=None):
        ok = gpu_works or device != "cuda"
        m = _FakeModel(name, device, compute_type, ok=ok)
        if built is not None:
            built.append(m)
        return m
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = _factory
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


def _install_fake_ct2(monkeypatch, device_count):
    mod = types.ModuleType("ctranslate2")
    mod.get_cuda_device_count = lambda: device_count
    monkeypatch.setitem(sys.modules, "ctranslate2", mod)


def _install_cpu_only_torch(monkeypatch):
    """The exact environment that caused the bug: torch present, CUDA absent."""
    mod = types.ModuleType("torch")
    mod.__version__ = "2.12.0+cpu"
    mod.cuda = types.SimpleNamespace(
        is_available=lambda: False,
        mem_get_info=lambda: (_ for _ in ()).throw(RuntimeError("no cuda")),
    )
    monkeypatch.setitem(sys.modules, "torch", mod)


# --- the regression ---------------------------------------------------------

def test_cpu_only_torch_does_not_veto_a_working_gpu(monkeypatch):
    from src.transcribe import Transcriber, WhisperConfig
    _install_cpu_only_torch(monkeypatch)
    _install_fake_ct2(monkeypatch, device_count=1)
    _install_fake_whisper(monkeypatch)

    t = Transcriber(WhisperConfig(model="auto", device="auto", compute_type="auto"))

    assert t.resolved_device == "cuda"
    assert t.resolved_model == "large-v3-turbo"
    assert t.model.compute_type == "float16"


def test_no_cuda_device_resolves_to_cpu(monkeypatch):
    from src.transcribe import Transcriber, WhisperConfig
    _install_fake_ct2(monkeypatch, device_count=0)
    _install_fake_whisper(monkeypatch)

    t = Transcriber(WhisperConfig(model="auto", device="auto", compute_type="auto"))

    assert t.resolved_device == "cpu"
    assert t.resolved_model == "base"
    assert t.model.compute_type == "int8"


def test_missing_ctranslate2_resolves_to_cpu(monkeypatch):
    from src.transcribe import Transcriber, WhisperConfig
    monkeypatch.setitem(sys.modules, "ctranslate2", None)   # import raises
    _install_fake_whisper(monkeypatch)

    t = Transcriber(WhisperConfig(model="auto", device="auto", compute_type="auto"))

    assert t.resolved_device == "cpu"


# --- the failure mode that loses a dictation --------------------------------

def test_broken_cuda_libs_fall_back_to_cpu_with_cpu_settings(monkeypatch):
    """A device that exists but cannot load cuBLAS must not reach a dictation.

    The fallback has to re-resolve model AND compute type: leaving float16 or
    large-v3-turbo behind would fail again on the CPU build.
    """
    from src.transcribe import Transcriber, WhisperConfig
    _install_fake_ct2(monkeypatch, device_count=1)
    built: list[_FakeModel] = []
    _install_fake_whisper(monkeypatch, gpu_works=False, built=built)

    t = Transcriber(WhisperConfig(model="auto", device="auto", compute_type="auto"))

    assert t.resolved_device == "cpu"
    assert t.resolved_model == "base"
    assert t.model.compute_type == "int8"
    assert [m.device for m in built] == ["cuda", "cpu"]   # tried GPU, then fell back


def test_explicitly_pinned_cuda_still_falls_back(monkeypatch):
    """Slower than asked beats broken. Pinning cuda must not break dictation."""
    from src.transcribe import Transcriber, WhisperConfig
    _install_fake_ct2(monkeypatch, device_count=1)
    _install_fake_whisper(monkeypatch, gpu_works=False)

    t = Transcriber(WhisperConfig(model="auto", device="cuda", compute_type="auto"))

    assert t.resolved_device == "cpu"


def test_probe_disables_vad_so_a_broken_gpu_cannot_pass(monkeypatch):
    """With the VAD on, silence yields no segments and the encoder never runs.

    The probe would then pass on a GPU that cannot encode, which is the whole
    thing it exists to catch.
    """
    from src.transcribe import Transcriber, WhisperConfig
    _install_fake_ct2(monkeypatch, device_count=1)
    built: list[_FakeModel] = []
    _install_fake_whisper(monkeypatch, built=built)

    Transcriber(WhisperConfig(model="auto", device="auto", compute_type="auto"))

    probe_kwargs = built[0].calls[0]
    assert probe_kwargs["vad_filter"] is False


# --- explicit config is honoured -------------------------------------------

@pytest.mark.parametrize("device,model,compute", [
    ("cuda", "large-v3-turbo", "float16"),
    ("cpu", "base", "int8"),
])
def test_resolve_auto_values_per_device(device, model, compute):
    from src.transcribe import WhisperConfig, _resolve
    assert _resolve(WhisperConfig(model="auto", compute_type="auto"), device) == (model, compute)


def test_explicit_model_and_compute_are_not_overridden():
    from src.transcribe import WhisperConfig, _resolve
    cfg = WhisperConfig(model="small", compute_type="int8_float16")
    assert _resolve(cfg, "cuda") == ("small", "int8_float16")
