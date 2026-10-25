"""Shared harness for the load tests: a real threaded werkzeug server on a free
loopback port, a seeded throwaway History, and a "100 clients at once" driver.

Nothing here touches the user's live app, config, database or GPU: every test
builds its own app on tmp_path with stub models.
"""
from __future__ import annotations

import random
import shutil
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.history import History

CLIENTS = 100
REPO_CFG = (Path(__file__).resolve().parents[2]
            / "packaging" / "default" / "config.yaml")

WORDS = ("meeting budget refactor parser release deploy notes client invoice "
         "schedule review design sprint launch draft email python graph").split()


@dataclass
class Outcome:
    status: int | None
    seconds: float
    error: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def statuses(self) -> dict:
        out: dict = {}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return out

    @property
    def errors(self) -> list[str]:
        return [o.error for o in self.outcomes if o.error]

    def percentile(self, q: float) -> float:
        xs = sorted(o.seconds for o in self.outcomes)
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    def summary(self) -> str:
        return (f"n={len(self.outcomes)} statuses={self.statuses} "
                f"p50={self.percentile(.5):.3f}s p95={self.percentile(.95):.3f}s "
                f"max={self.percentile(1):.3f}s errors={len(self.errors)}")


def hammer(request_fn, n: int = CLIENTS) -> Report:
    """Run request_fn(i) from n threads released at the same instant.

    request_fn returns an HTTP status (or raises). Every call is timed; an
    exception is recorded as status None with its message, never swallowed.
    """
    barrier = threading.Barrier(n)

    def one(i: int) -> Outcome:
        barrier.wait()
        t0 = time.perf_counter()
        try:
            status = request_fn(i)
            return Outcome(status, time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001 - reported, not hidden
            return Outcome(None, time.perf_counter() - t0, f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=n) as pool:
        return Report(list(pool.map(one, range(n))))


@pytest.fixture
def serve():
    """serve(flask_app) -> base URL of a real threaded server; stopped after."""
    from werkzeug.serving import make_server
    servers = []

    def _start(flask_app) -> str:
        srv = make_server("127.0.0.1", 0, flask_app, threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def fake_embed(text: str) -> np.ndarray:
    """Deterministic 384-dim unit vector, so search ranks real vectors without
    loading sentence-transformers (5+ s, and absent on the minimal CI lanes)."""
    rng = np.random.default_rng(zlib.crc32(text.encode("utf-8")))
    v = rng.standard_normal(384).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _offline_embedder(monkeypatch):
    from src import retrieval
    monkeypatch.setattr(retrieval, "embed", fake_embed)


def seed_history(h: History, rows: int, seed: int = 7) -> None:
    from src import retrieval
    rng = random.Random(seed)
    now = time.time()
    data = []
    for i in range(rows):
        text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(5, 25)))
        data.append((now - i * 300, "Code.exe", "medium", "en", 2000, text, text,
                     "desktop" if i % 4 else "mobile", rng.randint(400, 3000),
                     retrieval.to_blob(fake_embed(text)), retrieval._model_name))
    h.conn.executemany(
        "INSERT INTO dictations(ts, window_title, style, language, duration_ms, "
        "raw_text, cleaned_text, source, latency_ms, embedding, embedding_model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        data)
    h.conn.commit()


class DashboardRef:
    """The attributes make_app reads from the daemon's App object."""

    def __init__(self, cfg, cfg_path, history):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.history = history
        self.retriever = None
        self.cleaner = None
        self._scratchpad_target_id = None

    def reload_config(self):
        pass

    def refresh_transform_hotkeys(self):
        pass


@pytest.fixture
def dashboard_ref(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(REPO_CFG, cfg_path)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["dashboard"]["onboarded"] = True
    h = History(str(tmp_path / "history.db"))
    seed_history(h, 2000)
    return DashboardRef(cfg, cfg_path, h)


@pytest.fixture
def no_thread_leak():
    """Fail if the test leaves non-daemon threads running."""
    before = {t.ident for t in threading.enumerate()}
    yield
    deadline = time.time() + 5
    while time.time() < deadline:
        extra = [t for t in threading.enumerate()
                 if t.ident not in before and t.is_alive() and not t.daemon]
        if not extra:
            return
        time.sleep(0.05)
    pytest.fail(f"threads left running: {[t.name for t in extra]}")
