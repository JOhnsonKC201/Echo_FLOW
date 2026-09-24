"""A stopped Ollama must cost well under a second per dictation.

On Windows a TCP connect to a closed local port is not refused at once: the
stack retries for about 2 s, per address, and `localhost` resolves to both
::1 and 127.0.0.1. With only a read timeout set, every dictation waited ~4 s
before falling back to raw text. A short connect timeout bounds that; the
read timeout stays long, since generation can legitimately take seconds.
"""
from __future__ import annotations

import socket
import time


def _cleaner(base_url: str, **ollama):
    from src.cleanup import Cleaner
    return Cleaner({"enabled": True, "provider": "ollama",
                    "ollama": {"model": "test-model", "timeout_sec": 8.0,
                               "base_url": base_url, **ollama}})


def _closed_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_connect_and_read_timeouts_are_separate(monkeypatch):
    c = _cleaner("http://127.0.0.1:11434")
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    def _post(url, json=None, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(c._session, "post", _post)
    c._via_ollama("sys", "text")
    connect, read = seen["timeout"]
    assert connect <= 1.0
    assert read == 8.0


def test_connect_timeout_is_configurable(monkeypatch):
    c = _cleaner("http://127.0.0.1:11434", connect_timeout_sec=0.25)
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}

    monkeypatch.setattr(c._session, "post",
                        lambda url, json=None, timeout=None: seen.update(t=timeout) or _Resp())
    c._via_ollama("sys", "text")
    assert seen["t"][0] == 0.25


def test_stopped_ollama_fails_fast(monkeypatch):
    monkeypatch.setattr("src.cleanup.notify.notify", lambda *a, **k: None)
    c = _cleaner(f"http://127.0.0.1:{_closed_port()}")
    t = time.perf_counter()
    out, _ = c.clean("ship the thing")
    elapsed = time.perf_counter() - t
    assert "ship the thing" in out.lower()
    assert elapsed < 1.5, f"a refused connection took {elapsed:.2f}s"
