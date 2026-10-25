"""100 simultaneous dashboard users against a real threaded server and a
2,000-dictation history: reads, searches, the graph, and writes all at once."""
from __future__ import annotations

import requests
import yaml

from src.dashboard.app import make_app
from tests.load.conftest import Report, hammer

import pytest

pytestmark = pytest.mark.load

HOST = {"Host": "127.0.0.1:8766"}
# Generous on purpose: CI runners are slow and shared. The point is "no
# request waits behind a pile-up", not a benchmark.
P95_BUDGET_S = 10.0

READS = ["/", "/insights", "/search/api?q=budget", "/dictionary", "/actions",
         "/settings/general", "/notifications", "/healthz"]


def _get(base, path):
    return requests.get(base + path, headers=HOST, timeout=60).status_code


# The graph is one shared t-SNE build (about 4.7 s here); a slow shared CI
# runner can take several times that.
GRAPH_BUDGET_S = 30.0


def _assert_healthy(report: Report, allowed=(200,), budget=P95_BUDGET_S):
    print(report.summary())
    assert report.errors == []
    assert set(report.statuses) <= set(allowed), report.statuses
    assert report.percentile(.95) < budget, report.summary()


def test_100_users_browsing_at_once(serve, dashboard_ref, no_thread_leak):
    base = serve(make_app(dashboard_ref))
    report = hammer(lambda i: _get(base, READS[i % len(READS)]))
    _assert_healthy(report)


def test_100_users_opening_the_graph_at_once(serve, dashboard_ref, monkeypatch):
    from src.dashboard import graph_obsidian
    builds = []
    real_render = graph_obsidian.render

    def counting_render(*a, **k):
        builds.append(1)
        return real_render(*a, **k)

    monkeypatch.setattr(graph_obsidian, "render", counting_render)
    base = serve(make_app(dashboard_ref))
    report = hammer(lambda i: _get(base, "/graph/raw"))
    _assert_healthy(report, budget=GRAPH_BUDGET_S)
    # One build shared by all 100, not a thundering herd of t-SNE runs.
    assert len(builds) == 1


def test_100_users_writing_at_once_keep_config_and_db_consistent(serve, dashboard_ref):
    base = serve(make_app(dashboard_ref))

    def write(i):
        if i % 2:
            r = requests.post(base + "/api/theme", headers=HOST, timeout=60,
                              data={"theme": "light" if i % 4 == 1 else "dark"})
        else:
            r = requests.post(base + "/dictionary/add", headers=HOST, timeout=60,
                              data={"term": f"LoadTerm{i}"}, allow_redirects=False)
        return r.status_code

    report = hammer(write)
    _assert_healthy(report, allowed=(200, 302, 303))

    # The config file survived 50 concurrent rewrites intact.
    cfg = yaml.safe_load(dashboard_ref.cfg_path.read_text(encoding="utf-8"))
    assert cfg["dashboard"]["theme"] in ("light", "dark")
    # Every one of the 50 dictionary writes landed exactly once.
    from src.dashboard import vocabulary
    terms = [t for t in vocabulary.all_terms(dashboard_ref.history.conn)
             if t.startswith("LoadTerm")]
    assert len(terms) == len(set(terms)) == 50


def test_mixed_read_write_storm(serve, dashboard_ref):
    base = serve(make_app(dashboard_ref))

    def mixed(i):
        if i % 5 == 0:
            return requests.post(base + "/dictionary/add", headers=HOST, timeout=60,
                                 data={"term": f"Mix{i}"},
                                 allow_redirects=False).status_code
        return _get(base, READS[i % len(READS)])

    _assert_healthy(hammer(mixed), allowed=(200, 302, 303))
