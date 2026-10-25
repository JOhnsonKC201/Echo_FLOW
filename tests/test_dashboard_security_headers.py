"""Every dashboard response carries anti-framing and content-type headers, and
the graph page loads D3 from the dashboard itself, never from a public CDN."""
from __future__ import annotations

import re

import pytest

from tests.test_dashboard_actions_view import HOST, _client

pytestmark = pytest.mark.unit

ROUTES = ["/", "/actions", "/settings/general", "/graph", "/graph/raw",
          "/healthz", "/static/app.css", "/no-such-page"]


@pytest.fixture
def client(tmp_path):
    c, _ = _client(tmp_path)
    return c


@pytest.mark.parametrize("path", ROUTES)
def test_response_forbids_cross_site_framing(client, path):
    r = client.get(path, headers=HOST)
    # SAMEORIGIN, not DENY: /graph embeds /graph/raw in a same-origin iframe.
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    csp = r.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'self'" in csp


@pytest.mark.parametrize("path", ROUTES)
def test_response_sets_nosniff_and_referrer_policy(client, path):
    r = client.get(path, headers=HOST)
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "same-origin"


def test_csp_allows_scripts_only_from_the_dashboard(client):
    csp = client.get("/", headers=HOST).headers["Content-Security-Policy"]
    script_src = re.search(r"script-src ([^;]+)", csp).group(1).split()
    assert set(script_src) <= {"'self'", "'unsafe-inline'"}
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_graph_page_loads_no_third_party_script(client):
    html = client.get("/graph/raw", headers=HOST).get_data(as_text=True)
    srcs = re.findall(r"<script[^>]*\bsrc=\"([^\"]+)\"", html)
    assert srcs, "the graph page should load D3"
    assert all(s.startswith("/static/") for s in srcs), srcs


def test_vendored_d3_is_served(client):
    r = client.get("/static/vendor/d3.v7.min.js", headers=HOST)
    assert r.status_code == 200
    assert r.get_data(as_text=True).startswith("// https://d3js.org v7.")
