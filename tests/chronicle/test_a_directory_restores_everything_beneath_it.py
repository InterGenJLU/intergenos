# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A named directory in a restore brings back everything beneath it, and nothing beside it.

WHY THIS FILE EXISTS. Measured on an installed machine while walking a backup
leg: a person who asks for a folder back gets the folder's metadata and none of
its files, and the only instruction offered is to name every stored path by
hand. A version holding thousands of paths makes that instruction useless, and
the person is left reading the store directly — which is the thing the engine
exists to prevent. The prefix sibling in this corpus is the other half of the
requirement: "everything beneath it" must not become "everything whose name
starts the same way".
"""

from pathlib import Path

import pytest

from chronicle import config, engine, escalate, paths


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    home = tmp_path / "home"
    documents = home / "documents"
    (documents / "sub").mkdir(parents=True)
    (documents / "note").write_bytes(b"saved note\n")
    (documents / "sub" / "deep").write_bytes(b"saved deep\n")
    sibling = home / "documents-other"
    sibling.mkdir()
    (sibling / "keep").write_bytes(b"saved sibling\n")
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "other").write_bytes(b"saved other\n")

    cfg = config.Config()
    cfg.user_data_paths = [str(home)]
    eng = engine.Engine(local_root=tmp_path / "local", config=cfg)
    target = tmp_path / "target"
    target.mkdir()
    eng.target_adopt(target, target_class="directory")
    version = eng.capture(paths.LAYER_USER_DATA)["version_id"]

    # Every live file now differs from its stored copy, so a restore is observable
    # and an unasked-for restore is equally observable.
    for live in (documents / "note", documents / "sub" / "deep",
                 sibling / "keep", elsewhere / "other"):
        live.write_bytes(b"live " + live.name.encode() + b"\n")

    # The capability is the restore leg's, not this test's; ownership application
    # is best-effort in the engine and stays so here.
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *a, **k: pytest.fail("restore escalated instead of running"))
    return eng, version, home, documents, sibling, elsewhere


def _planned(eng, version, request):
    plan = eng.restore_plan(paths.LAYER_USER_DATA, version, [request])
    return plan, [a["path"] for a in plan["actions"]]


@pytest.mark.parametrize("trailing_slash", [False, True])
def test_the_plan_names_every_stored_path_beneath_the_directory(corpus, trailing_slash):
    eng, version, _home, documents, sibling, elsewhere = corpus
    request = str(documents) + ("/" if trailing_slash else "")
    plan, planned = _planned(eng, version, request)

    assert str(documents / "note") in planned
    assert str(documents / "sub" / "deep") in planned
    assert [a for a in plan["actions"] if a["action"] == "skip"] == []
    assert len(planned) == len(set(planned)), "a path is planned twice"

    beside = [p for p in planned
              if p.startswith(str(sibling)) or p.startswith(str(elsewhere))]
    assert beside == [], f"paths beside the directory were planned: {beside}"

    directory_action = next(a for a in plan["actions"]
                            if a["path"] == str(documents))
    assert directory_action["type"] == "dir"
    assert directory_action["subtree"] == len(planned) - 1


def test_restoring_the_directory_brings_back_every_file_beneath_it(corpus):
    eng, version, _home, documents, sibling, elsewhere = corpus
    result = eng.restore_apply(paths.LAYER_USER_DATA, version, [str(documents)])

    failed = [r for r in result["results"] if not r["ok"]]
    assert failed == [], f"paths not restored: {failed}"
    assert (documents / "note").read_bytes() == b"saved note\n"
    assert (documents / "sub" / "deep").read_bytes() == b"saved deep\n"
    # Nothing beside it moved.
    assert (sibling / "keep").read_bytes() == b"live keep\n"
    assert (elsewhere / "other").read_bytes() == b"live other\n"


def test_naming_a_file_beneath_the_directory_still_restores_that_file_alone(corpus):
    eng, version, _home, documents, sibling, _elsewhere = corpus
    result = eng.restore_apply(paths.LAYER_USER_DATA, version,
                               [str(documents / "note")])
    assert [r["path"] for r in result["results"]] == [str(documents / "note")]
    assert (documents / "note").read_bytes() == b"saved note\n"
    assert (documents / "sub" / "deep").read_bytes() == b"live deep\n"


def test_naming_the_directory_and_a_file_beneath_it_plans_each_path_once(corpus):
    eng, version, _home, documents, _sibling, _elsewhere = corpus
    plan = eng.restore_plan(paths.LAYER_USER_DATA, version,
                            [str(documents), str(documents / "note")])
    planned = [a["path"] for a in plan["actions"]]
    assert planned.count(str(documents / "note")) == 1


def test_a_directory_absent_from_the_version_is_still_reported_absent(corpus):
    eng, version, home, _documents, _sibling, _elsewhere = corpus
    absent = home / "never-captured"
    absent.mkdir()
    action = eng.restore_plan(paths.LAYER_USER_DATA, version,
                              [str(absent)])["actions"][0]
    assert action["action"] == "skip"
    assert action["reason"] == "not in this version"
