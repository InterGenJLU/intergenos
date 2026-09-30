# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A capture never commits a version that is silently short of the source.

MEASURED on an installed machine on 2026-09-30, against the shipped engine and
against this tree: a directory the walk cannot read is dropped without a trace.
One locked directory of 200 files turned a 2762-file corpus into a committed
version reporting 2562 files, at exit 0, with no field in the manifest naming
the omission and a timeline line a person reads as a successful backup. The
asymmetry is the tell: an unreadable FILE fails the capture loudly, because the
open() raises; an unreadable DIRECTORY vanishes, because `os.walk` without an
`onerror` argument swallows the error from its own listing and yields nothing
for that subtree.

WHAT THESE TESTS PROVE: that a capture which could not read part of its source
records every such path in the committed manifest, and reports the count; that a
capture which read everything records none; and that the root hash of a capture
with nothing unreadable is byte-identical to what this engine computed before
this change, so every manifest already on disk still verifies.

WHAT THEY DO NOT PROVE: the shipped-configuration trigger. The capture runs
inside chronicled holding CAP_DAC_READ_SEARCH, which defeats a plain permission
denial; the errors that capability does not cure — an I/O error, a refusing fuse
mount, a stale network mount — are not reproduced here.
"""

import json
import os

import pytest

from chronicle import configstate, cas, manifest, paths, userdata

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="the locked directory below is readable by root, so the case cannot "
           "be posed as this user")


@pytest.fixture
def corpus(tmp_path):
    """Twenty readable files and a directory of ten that will be locked."""
    source = tmp_path / "home"
    (source / "readable").mkdir(parents=True)
    for i in range(20):
        (source / "readable" / f"note-{i:02d}.txt").write_bytes(b"kept %d\n" % i)
    (source / "locked").mkdir()
    for i in range(10):
        (source / "locked" / f"secret-{i:02d}.txt").write_bytes(b"lost %d\n" % i)
    return source


def _capture(source, target, sequence=1):
    vid = userdata.capture([str(source)], target, None, sequence, 100, "reason")
    return manifest.find_version(target, paths.LAYER_USER_DATA, vid)


def test_a_fully_readable_capture_records_nothing_unreadable(corpus, tmp_path):
    m = _capture(corpus, tmp_path / "target")
    assert m["unreadable"] == []
    assert sum(1 for e in m["entries"] if e["type"] == "file") == 30


def test_the_capture_records_the_directory_it_could_not_read(corpus, tmp_path):
    locked = corpus / "locked"
    os.chmod(locked, 0o000)
    try:
        m = _capture(corpus, tmp_path / "target")
    finally:
        os.chmod(locked, 0o755)
    recorded = [u["path"] for u in m["unreadable"]]
    assert recorded == [str(locked)], (
        "the capture dropped a directory it could not read and the manifest "
        f"names {recorded}; a version that is short of its source and does not "
        "say so is a backup the person cannot know is incomplete"
    )
    assert all(u["error"] for u in m["unreadable"]), (
        "each recorded path states what went wrong, or a person cannot tell a "
        "permission problem from a failing disk"
    )
    captured = sum(1 for e in m["entries"] if e["type"] == "file")
    assert captured == 20, (
        f"the ten files under the locked directory are absent ({captured} of 30 "
        "captured), which is exactly why the omission has to be recorded"
    )


def test_the_recorded_paths_are_exactly_the_ones_that_could_not_be_read(
        corpus, tmp_path):
    """The control: the record must name the locked directory and nothing else."""
    (corpus / "another").mkdir()
    (corpus / "another" / "fine.txt").write_bytes(b"fine\n")
    locked = corpus / "locked"
    os.chmod(locked, 0o000)
    try:
        m = _capture(corpus, tmp_path / "target")
    finally:
        os.chmod(locked, 0o755)
    assert [u["path"] for u in m["unreadable"]] == [str(locked)]
    paths_captured = {e["path"] for e in m["entries"]}
    assert str(corpus / "another" / "fine.txt") in paths_captured


def test_the_root_hash_of_a_clean_capture_is_what_it_always_was(corpus, tmp_path):
    """Backward compatibility, proven rather than asserted: with nothing
    unreadable, the root hash is the sha256 over the entry serialization alone,
    exactly as every manifest already on disk was written."""
    m = _capture(corpus, tmp_path / "target")
    assert m["unreadable"] == []
    assert m["root_hash"] == cas.sha256_bytes(
        manifest.canonical_bytes(m["entries"]))


def test_an_unreadable_path_changes_the_root_hash(corpus, tmp_path):
    """And the record is covered by the integrity hash, so stripping it from a
    committed manifest while leaving the root hash makes the manifest stop
    verifying."""
    locked = corpus / "locked"
    os.chmod(locked, 0o000)
    try:
        m = _capture(corpus, tmp_path / "target")
    finally:
        os.chmod(locked, 0o755)
    assert m["unreadable"]
    assert m["root_hash"] != cas.sha256_bytes(
        manifest.canonical_bytes(m["entries"])), (
        "the unreadable record is outside the root hash, so anything that can "
        "edit the manifest can hide the omission and still verify"
    )


def test_config_state_capture_records_what_it_could_not_read(tmp_path):
    base = tmp_path / "etc"
    (base / "readable").mkdir(parents=True)
    (base / "readable" / "conf").write_bytes(b"a\n")
    (base / "locked").mkdir()
    (base / "locked" / "hidden").write_bytes(b"b\n")
    store_root = tmp_path / "store"
    paths.ensure_store_skeleton(store_root)
    store = cas.ContentStore(store_root)
    os.chmod(base / "locked", 0o000)
    try:
        vid = configstate.capture(
            [str(base)], store_root, store, 1, 100, "reason", excludes=())
    finally:
        os.chmod(base / "locked", 0o755)
    m = manifest.find_version(store_root, paths.LAYER_CONFIG_STATE, vid)
    assert [u["path"] for u in m["unreadable"]] == [str(base / "locked")]


# --------------------------------------------------------------------------
# The person-facing surface: a short version must be visible without reading
# the store by hand.
# --------------------------------------------------------------------------


@pytest.fixture
def engine_with_target(tmp_path):
    from chronicle import config as _config, engine as _engine
    source = tmp_path / "home"
    (source / "readable").mkdir(parents=True)
    for i in range(5):
        (source / "readable" / f"n{i}.txt").write_bytes(b"x\n")
    (source / "locked").mkdir()
    (source / "locked" / "hidden.txt").write_bytes(b"y\n")
    cfg = _config.Config()
    cfg.user_data_paths = [str(source)]
    eng = _engine.Engine(local_root=tmp_path / "store", config=cfg)
    eng.target_adopt(str(tmp_path / "target"), target_class="directory",
                     cap_bytes=1 << 30)
    return eng, source


def test_capture_hands_back_the_paths_it_could_not_read(engine_with_target):
    eng, source = engine_with_target
    os.chmod(source / "locked", 0o000)
    try:
        res = eng.capture(paths.LAYER_USER_DATA, None, "reason")
    finally:
        os.chmod(source / "locked", 0o755)
    assert [u["path"] for u in res["unreadable"]] == [str(source / "locked")], (
        "the capture result does not name what it missed, so the only place a "
        "person could learn it is the store itself"
    )


def test_the_timeline_carries_the_count_for_a_short_version(engine_with_target):
    eng, source = engine_with_target
    os.chmod(source / "locked", 0o000)
    try:
        eng.capture(paths.LAYER_USER_DATA, None, "the short one")
    finally:
        os.chmod(source / "locked", 0o755)
    eng.capture(paths.LAYER_USER_DATA, None, "the complete one")
    timeline = {v["reason"]: v for v in eng.list_versions(paths.LAYER_USER_DATA)}
    assert timeline["the short one"]["unreadable"] == 1, (
        "the timeline shows a short version exactly as it shows a complete one"
    )
    assert timeline["the complete one"]["unreadable"] == 0
    assert (timeline["the complete one"]["files"]
            > timeline["the short one"]["files"])


def test_the_timeline_reports_a_count_and_never_a_path(engine_with_target):
    """list is a .read-tier verb and the shipped policy keeps paths out of that
    tier, so the count is all it may carry."""
    eng, source = engine_with_target
    os.chmod(source / "locked", 0o000)
    try:
        eng.capture(paths.LAYER_USER_DATA, None, "reason")
    finally:
        os.chmod(source / "locked", 0o755)
    row = eng.list_versions(paths.LAYER_USER_DATA)[0]
    assert row["unreadable"] == 1
    assert "locked" not in json.dumps(row)
