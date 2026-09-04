"""Put the pip-installed NVIDIA runtime DLLs where CTranslate2 can find them.

faster-whisper runs on CTranslate2, which links cuBLAS and cuDNN lazily, at the
first *inference* call rather than at model load. When those DLLs are missing
the model constructs happily and then dies partway through a dictation with:

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

The libraries ship in the ``nvidia-cublas-cu12`` and ``nvidia-cudnn-cu12``
wheels, which unpack them to ``site-packages/nvidia/<lib>/bin``. Windows does
not search that location on its own.

``os.add_dll_directory`` does NOT fix this. CTranslate2 loads the libraries from
inside its own compiled extension, whose search path is fixed when that
extension is imported, so a directory added afterwards is never consulted.
Prepending to ``PATH`` before CTranslate2 is imported is what actually works,
which is why `ensure()` has to run ahead of the faster-whisper import.
"""
from __future__ import annotations

import glob
import os

_cached: list[str] | None = None


def ensure() -> list[str]:
    """Prepend the NVIDIA wheel DLL directories to PATH.

    Idempotent: the directories are located once and the same list is returned
    on every later call. Returns the directories added, which is empty when the
    wheels are not installed (a CPU-only install, where this is a no-op) or on
    a non-Windows platform, where the loader resolves them via RPATH instead.
    """
    global _cached
    if _cached is not None:
        return _cached
    if os.name != "nt":
        _cached = []
        return _cached
    try:
        import nvidia  # namespace package: has __path__ but no __file__
    except Exception:
        _cached = []
        return _cached
    dirs: list[str] = []
    for base in list(getattr(nvidia, "__path__", None) or []):
        for d in sorted(glob.glob(os.path.join(base, "*", "bin"))):
            if os.path.isdir(d):
                dirs.append(d)
    if dirs:
        os.environ["PATH"] = (
            os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
        )
    _cached = dirs
    return _cached
