"""Keep package command tests away from installed transaction handlers, and
away from the update advisory of the machine running them."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest


# An environment override also covers fresh imports and child processes. The
# command paths resolve it at runtime, including commands with an install root.
_PRETXN_TMP = tempfile.mkdtemp(prefix="pkm-pytest-pretxn-")
atexit.register(lambda: shutil.rmtree(_PRETXN_TMP, ignore_errors=True))
os.environ["PKM_PRETXN_HANDLER_DIR"] = _PRETXN_TMP


@pytest.fixture(autouse=True)
def _no_test_reaches_this_machines_update_advisory(monkeypatch):
    """Fail a test whose command would refresh or write the update advisory of
    the machine running the suite, and stop the write.

    A command driven in-process with no install root resolves the advisory to
    /var/lib/pkm/available-updates.json. The refresh after a transaction turns
    every error into a warning, so without privilege the attempt is hidden in
    output the test captures, and as root it would overwrite the real file
    with a count computed from a test database. A test names its own root
    (cli.set_install_root) or stubs the refresh when the advisory is not what
    it tests.
    """
    from pkm import cli, rootpaths

    machine = Path(rootpaths.available_updates_path(rootpaths.DEFAULT_ROOT))
    reached = []
    refresh = cli.refresh_available_updates_after_transaction
    write = cli._write_available_updates_json

    def guarded_refresh(db, output_path=None):
        target = Path(output_path if output_path is not None
                      else cli.available_updates_path())
        if target == machine:
            reached.append(f"a refresh of {target}")
            return None
        return refresh(db, output_path)

    def guarded_write(summary, output_path):
        if Path(output_path) == machine:
            reached.append(f"a write of {output_path}")
            return None
        return write(summary, output_path)

    monkeypatch.setattr(cli, "refresh_available_updates_after_transaction",
                        guarded_refresh)
    monkeypatch.setattr(cli, "_write_available_updates_json", guarded_write)
    yield
    if reached:
        pytest.fail("the test reached the update advisory of the machine "
                    "running the suite: " + "; ".join(reached), pytrace=False)
