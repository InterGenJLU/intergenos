"""Keep the source-fetcher tests from spending real seconds in retry backoff.

download_file() waits between upstream attempts on purpose — a server shedding
load needs a moment before the next ask. A test that exercises a failing fetch
would otherwise sit through those waits for nothing, so the wait is set to zero
for every test in this directory. Tests that care about the pause set the
variable themselves.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    monkeypatch.setenv("SOURCE_FETCH_BACKOFF", "0")
