"""The test suite must never write into the running daemon's log."""
from __future__ import annotations

import logging
from pathlib import Path


def test_the_suite_does_not_log_into_the_daemons_directory():
    """Fixture errors ("cuda went away") used to land in the real wispr.log."""
    from src import main  # noqa: F401  (its import is what calls log.setup)
    repo_data = (Path(__file__).resolve().parent.parent / "data").resolve()
    files = [Path(h.baseFilename).resolve() for h in logging.getLogger("wispr").handlers
             if isinstance(h, logging.FileHandler)]
    assert files, "log.setup() did not attach a file handler"
    assert all(repo_data not in f.parents for f in files), files
