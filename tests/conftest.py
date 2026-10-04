"""Tests rebuild outputs/ while they run. This session hook snapshots outputs/, web/data/ and
data/new_laws/ before the first test and restores them exactly afterwards, so running
`make test` can never change or destroy the files you are about to submit."""
import shutil
import tempfile

from citemap import config

_SNAP = {}
_DIRS = {"outputs": config.OUTPUTS, "web_data": config.ROOT / "web" / "data", "new_laws": config.NEW_LAWS_DIR}


def pytest_sessionstart(session):
    tmp = tempfile.mkdtemp(prefix="citemap-snap-")
    for k, d in _DIRS.items():
        if d.exists():
            _SNAP[k] = shutil.copytree(d, f"{tmp}/{k}")


def pytest_sessionfinish(session, exitstatus):
    for k, d in _DIRS.items():
        shutil.rmtree(d, ignore_errors=True)
        if k in _SNAP:
            shutil.copytree(_SNAP[k], d)
