"""Opt-in self-update check — the one network call Echo Flow makes about itself.

Echo Flow's entire identity is "nothing leaves this box." This module is the
single deliberate exception, and it is **OFF by default**. Only when the user
sets ``update.check_on_startup: true`` in ``config.yaml`` does the daemon make
exactly one anonymous HTTPS GET to the public GitHub Releases API at launch to
see whether a newer version exists — and, if ``update.notify`` is true, show a
tray toast.

Hard guarantees (all covered by tests in ``tests/test_update_check.py``):

* **Default off.** With no ``update:`` block, or ``check_on_startup: false``,
  this module makes zero network calls — ``maybe_check_async`` returns ``None``
  before touching anything.
* **No payload about the user ever leaves the machine.** It's an anonymous GET
  of a public endpoint — no history, no config, no identifiers.
* **Never blocks startup, never raises into the caller.** The check runs on a
  short-timeout daemon thread and swallows every error.
* **Honestly reflected in the privacy ledger** (see ``src/dashboard/privacy.py``)
  so the /privacy page stops claiming zero egress when this is enabled.
"""
from __future__ import annotations

import re
import threading
from typing import Callable, Optional

from . import log as _wlog

_log = _wlog.get("update")

# Canonical public repo — anonymous, unauthenticated, read-only.
REPO = "JOhnsonKC201/Echo_FLOW"
LATEST_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO}/releases/latest"

# A slow or unreachable network must never delay the toast or pin a thread for
# long. The check is best-effort and entirely skippable.
_DEFAULT_TIMEOUT = 4.0

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(text: object) -> tuple[int, int, int]:
    """Parse a loose semver string into a comparable ``(major, minor, patch)``.

    Tolerant by design: strips a leading ``v``, ignores any pre-release/build
    suffix (``1.2.3-rc1`` -> ``(1, 2, 3)``), and treats missing components as 0.
    Returns ``(0, 0, 0)`` for anything unparseable, so a garbage tag can never
    be read as "newer".
    """
    if not text:
        return (0, 0, 0)
    m = _VERSION_RE.search(str(text))
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def is_newer(latest: object, current: object) -> bool:
    """True iff ``latest`` is a strictly higher version than ``current``."""
    return parse_version(latest) > parse_version(current)


def enabled(cfg: dict | None) -> bool:
    """Whether the opt-in startup update check is turned on in config."""
    u = (cfg or {}).get("update") or {}
    return bool(u.get("check_on_startup", False))


def check_for_update(
    current_version: str,
    *,
    url: str = LATEST_API_URL,
    timeout: float = _DEFAULT_TIMEOUT,
    session: object | None = None,
) -> Optional[dict]:
    """Perform one anonymous GET against the GitHub Releases API.

    Returns ``{"current", "latest", "url", "is_newer"}`` on success, or ``None``
    on any failure (network error, timeout, non-200, malformed JSON, missing
    tag). Never raises. ``session`` (anything with a ``.get``) is injectable for
    tests; production passes ``None`` and uses ``requests`` directly.
    """
    try:
        import requests  # lazy: never imported unless the check actually runs
    except Exception as e:  # pragma: no cover - requests is a hard runtime dep
        _log.debug("update check: requests unavailable: %s", e)
        return None
    try:
        getter = session.get if session is not None else requests.get
        resp = getter(
            url,
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"EchoFlow/{current_version}",
            },
        )
        if resp.status_code != 200:
            _log.debug("update check: HTTP %s", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        _log.debug("update check: request failed: %s", e)
        return None
    tag = (data or {}).get("tag_name") or (data or {}).get("name")
    if not tag:
        return None
    html_url = (data or {}).get("html_url") or RELEASES_PAGE_URL
    return {
        "current": current_version,
        "latest": str(tag),
        "url": html_url,
        "is_newer": is_newer(tag, current_version),
    }


def maybe_check_async(
    cfg: dict | None,
    current_version: str,
    *,
    notify: Optional[Callable[[str, str, str], None]] = None,
    url: str = LATEST_API_URL,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[threading.Thread]:
    """If enabled in config, run ``check_for_update`` on a daemon thread.

    Returns the spawned thread (so callers/tests can ``join`` it), or ``None``
    when the feature is disabled — in which case **nothing** touches the
    network. A newer release triggers a single tray toast when ``update.notify``
    is true (the default).
    """
    if not enabled(cfg):
        return None
    u = (cfg or {}).get("update") or {}
    want_notify = bool(u.get("notify", True))

    def _worker() -> None:
        result = check_for_update(current_version, url=url, timeout=timeout)
        if not result or not result.get("is_newer"):
            return
        latest = result["latest"]
        _log.info("update available: %s (current %s)", latest, current_version)
        if want_notify and notify is not None:
            try:
                notify(
                    "Echo Flow update available",
                    f"Version {latest} is out (you have {current_version}). "
                    f"Get it from the GitHub releases page.",
                    "info",
                )
            except Exception as e:  # pragma: no cover - toast backend best-effort
                _log.debug("update toast failed: %s", e)

    t = threading.Thread(target=_worker, name="update-check", daemon=True)
    t.start()
    return t
