# SPDX-License-Identifier: GPL-3.0-or-later
"""The capture handler's reported identity crosses the process boundary."""

import pytest
from pathlib import Path

from pkm import pretxn
from pkm.database import PackageDB


@pytest.mark.parametrize("success, reported, expected", [
    (True, "0000000042-abcdef123456", ("0000000042-abcdef123456",)),
    (False, "0000000042-abcdef123456", ()),
    (True, "?", ()),
])
def test_successful_handler_identity_is_retained(tmp_path, success, reported, expected):
    handlers = tmp_path / "handlers"
    handlers.mkdir()
    handler = handlers / "chronicle"
    handler.write_text(
        "#!/usr/bin/python3\nimport json, sys\n"
        "request = json.load(sys.stdin)\n"
        "assert request['verb'] == 'upgrade'\n"
        f"print('chronicle: restore point {reported} captured for upgrade', file=sys.stderr)\n"
        f"sys.exit({0 if success else 1})\n")
    handler.chmod(0o755)
    root = tmp_path / "root"
    payload = root / "usr/bin/widget"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"installed before capture\n")
    with PackageDB(root / "var/lib/igos/pkm.db", root=str(root)) as db:
        package = db.add_installed("widget", "1.0", release=3)
        db.add_files(package, ["usr/bin/widget"])
        result = pretxn.run_pre_transaction_hook(
            db, "upgrade", ["widget"], "upgrade widget", handler_dir=handlers)
    assert getattr(result, "restore_points", None) == expected
    assert bool(result.failures) == (not success)
    if expected:
        assert expected[0] in result.messages[0]


def test_shipped_handler_identity_reaches_pkm_from_a_real_capture(tmp_path):
    source = Path(__file__).resolve().parents[2] / "assets/intergenos-backup"
    bytecode_before = {str(p): p.read_bytes() for p in source.rglob("*.pyc")}
    cache_dirs_before = set(source.rglob("__pycache__"))
    handlers = tmp_path / "handlers"
    handlers.mkdir()
    store = tmp_path / "store"
    flags = tmp_path / "handler-dont-write-bytecode"
    handler = handlers / "chronicle"
    handler.write_text(
        "#!/usr/bin/python3 -B\nimport runpy, sys\n"
        f"open({str(flags)!r}, 'w').write(str(sys.flags.dont_write_bytecode))\n"
        f"sys.path.insert(0, {str(source)!r})\n"
        "from chronicle import config, engine\n"
        f"eng = engine.Engine(local_root={str(store)!r}, config=config.Config())\n"
        f"handler = runpy.run_path({str(source / 'chronicle-pretxn-handler')!r})\n"
        "handler['main'].__globals__['_capture'] = lambda footprint: eng.capture(\n"
        "    'restore-point', scope=footprint, reason=footprint['reason'], sync=True)\n"
        "sys.exit(handler['main']())\n")
    handler.chmod(0o755)
    root = tmp_path / "root"
    payload = root / "usr/bin/widget"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"installed before capture\n")
    with PackageDB(root / "var/lib/igos/pkm.db", root=str(root)) as db:
        package = db.add_installed("widget", "1.0", release=3)
        db.add_files(package, ["usr/bin/widget"])
        result = pretxn.run_pre_transaction_hook(
            db, "upgrade", ["widget"], "upgrade widget", handler_dir=handlers)
    assert not result.failures, result.messages
    assert len(result.restore_points) == 1
    # Read the producer's persisted manifest, not only its output line.
    manifests = list(store.rglob("*.json"))
    assert any(result.restore_points[0] in p.read_text() for p in manifests)
    assert any(str(tmp_path / "root/var/lib/igos/pkm.db") in p.read_text()
               for p in manifests)
    # The handler itself ran with -B, read inside it: the comparison below sees
    # a missing -B only on a tree that held no cache before, because an
    # interpreter that finds a current .pyc does not rewrite it. The handler's
    # environment is the hook allowlist, so this reads its own command line.
    assert flags.read_text() == "1"
    assert set(source.rglob("__pycache__")) == cache_dirs_before
    assert {str(p): p.read_bytes() for p in source.rglob("*.pyc")} == bytecode_before
