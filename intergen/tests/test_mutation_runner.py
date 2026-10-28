# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Prove killed/surviving mutants and failure classification with real processes."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from intergen.tests import mutation_runner as runner

SOURCE = '''import re
PATTERN = r"^(?:blue|green|red)$"

def accepts(word):
    return re.fullmatch(PATTERN, word) is not None

def positive(number):
    if number > 0:
        return True
    return False

def answer():
    return 42

def untested():
    return "unused"
'''
CHECK = '''from subject import accepts, positive, answer
assert accepts("blue")
assert accepts("green")
assert accepts("red")
assert not accepts("yellow")
assert positive(3) and not positive(-1)
assert answer() == 42
'''
MUTATIONS = [
    {"id": "remove-green", "kind": "regex_alternative", "line": 2, "literal": "green"},
    {"id": "invert-positive", "kind": "invert_condition", "line": 8},
    {"id": "empty-answer", "kind": "return_none", "function": "answer"},
]
SURVIVOR = {"id": "empty-untested", "kind": "return_none", "function": "untested"}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "subject.py").write_text(SOURCE)
    (root / "check.py").write_text(CHECK)
    return root


def run(project, mutations=MUTATIONS, **kwargs):
    return runner.run_mutations(project, Path("subject.py"), mutations,
                                [sys.executable, "check.py"], **kwargs)


def test_each_mutant_is_killed_by_a_real_test_command(project):
    report = run(project)
    assert report["verdict"] == "PASS"
    assert report["baseline"]["returncode"] == 0
    assert [row["status"] for row in report["mutants"]] == ["KILLED"] * 3
    for row in report["mutants"]:
        assert row["returncode"] == 1
        assert "AssertionError" in row["output"]
        assert "--- a/subject.py" in row["diff"]
        assert "+++ b/subject.py" in row["diff"]
        assert row["command"][-1] == "check.py"


def test_designed_survivor_keeps_the_final_verdict_red(project):
    report = run(project, MUTATIONS + [SURVIVOR])
    assert report["verdict"] == "FAIL"
    assert [row["status"] for row in report["mutants"]] == ["KILLED"] * 3 + ["SURVIVED"]
    assert report["mutants"][-1]["returncode"] == 0


def test_original_and_existing_bytecode_are_unchanged_and_each_run_is_fresh(project):
    (project / "check.py").write_text(CHECK + '''
from pathlib import Path
assert not Path("run-marker").exists()
Path("run-marker").write_text("created in scratch only")
''')
    subprocess.run([sys.executable, "-m", "py_compile", str(project / "subject.py")], check=True)
    before = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in project.rglob("*") if p.is_file()}
    report = run(project, [SURVIVOR, *MUTATIONS])
    after = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in project.rglob("*") if p.is_file()}
    assert before == after
    assert [row["status"] for row in report["mutants"]] == ["SURVIVED"] + ["KILLED"] * 3


def test_baseline_failure_cannot_be_misreported_as_a_kill(project):
    (project / "check.py").write_text("raise AssertionError('baseline is red')\n")
    report = run(project)
    assert report["verdict"] == "ERROR"
    assert report["baseline"]["returncode"] == 1
    assert report["mutants"] == []


def test_timeout_is_an_error_not_a_kill(project):
    (project / "check.py").write_text('''from subject import answer
import time
if answer() is None:
    time.sleep(5)
''')
    report = run(project, [MUTATIONS[2]], timeout=0.2)
    assert report["verdict"] == "ERROR"
    assert report["mutants"][0]["status"] == "ERROR"
    assert "timeout" in report["mutants"][0]["error"]


def test_exit_two_is_an_error_not_a_kill(project):
    (project / "check.py").write_text("from subject import answer\nimport sys\nsys.exit(2 if answer() is None else 0)\n")
    report = run(project, [MUTATIONS[2]])
    assert report["verdict"] == "ERROR"
    assert report["mutants"][0]["status"] == "ERROR"


def test_missing_command_is_an_error(project):
    report = runner.run_mutations(project, Path("subject.py"), MUTATIONS,
                                  [str(project / "missing-command")])
    assert report["verdict"] == "ERROR"
    assert "error" in report["baseline"]


@pytest.mark.parametrize("mutations", [[], [SURVIVOR, SURVIVOR],
    [None], [{"kind": []}],
    [{"id": "missing", "kind": "return_none", "function": "missing"}],
    [{"id": "unknown", "kind": "unknown"}],
    [{"id": "missing", "kind": "invert_condition", "line": 2}],
    [{"id": "missing", "kind": "regex_alternative", "line": 2, "literal": "yellow"}],
    [{"id": "partial", "kind": "regex_alternative", "line": 2, "literal": "ree"}],
    [{"id": "bad-line", "kind": "invert_condition", "line": True}],
    [{"id": "bad-key", "kind": "return_none", "function": "answer", "typo": True}],
])
def test_invalid_mutations_cannot_produce_green(project, mutations):
    with pytest.raises(ValueError):
        run(project, mutations)


def test_target_outside_source_is_refused(project, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text(SOURCE)
    with pytest.raises(ValueError, match="within"):
        runner.run_mutations(project, outside, MUTATIONS, [sys.executable, "check.py"])


def test_absolute_test_path_is_mapped_to_the_scratch_copy(project):
    report = runner.run_mutations(project, project / "subject.py", MUTATIONS,
                                  [sys.executable, str(project / "check.py")])
    assert report["verdict"] == "PASS"
    assert all(str(project) not in row["command"][-1] for row in report["mutants"])


def test_internal_absolute_symlink_is_rebased_and_external_one_is_refused(project, tmp_path):
    link = project / "alias.py"
    link.symlink_to(project / "subject.py")
    (project / "check.py").write_text("from alias import answer\nassert answer() == 42\n")
    assert run(project, [MUTATIONS[2]])["verdict"] == "PASS"
    assert link.readlink() == project / "subject.py"
    outside = tmp_path / "outside.py"
    outside.write_text(SOURCE)
    (project / "external.py").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        run(project)


@pytest.mark.parametrize("pattern,literal,expected", [
    ("blue|green|red", "blue", "green|red"),
    ("blue|green|red", "green", "blue|red"),
    ("blue|green|red", "red", "blue|green"),
    (r"^(?:blue|green|red)$", "green", r"^(?:blue|red)$"),
    (r"(?:a\|b|c)", r"a\|b", r"(?:c)"),
    (r"[a|b]|c", "c", r"[a|b]"),
    (r"(?P<colour>blue|red)", "blue", r"(?P<colour>red)"),
])
def test_regex_removal_preserves_the_rest_of_the_pattern(pattern, literal, expected):
    source = f"PATTERN = {pattern!r}\n"
    mutant = runner.mutate(source, {"id": "regex", "kind": "regex_alternative",
                                    "line": 1, "literal": literal})
    namespace = {}
    exec(mutant, namespace)
    assert namespace["PATTERN"] == expected


def test_regex_ambiguity_and_a_sole_branch_are_refused():
    for pattern in ("blue", "blue|blue", "(blue|red)(blue|green)"):
        with pytest.raises(ValueError):
            runner.mutate(f"PATTERN = {pattern!r}\n",
                          {"id": "regex", "kind": "regex_alternative", "line": 1, "literal": "blue"})


def test_multiline_condition_and_unicode_columns_are_mutated():
    source = 'label = "é"\nif (\n    len(label) == 1\n):\n    result = True\nelse:\n    result = False\n'
    mutant = runner.mutate(source, {"id": "invert", "kind": "invert_condition", "line": 2})
    namespace = {}
    exec(mutant, namespace)
    assert namespace["result"] is False
    assert 'label = "é"' in mutant


def test_qualified_function_and_one_line_body_are_supported():
    source = 'class Thing:\n    def answer(self): return "é"\n\ndef answer():\n    return 42\n'
    mutant = runner.mutate(source, {"id": "body", "kind": "return_none", "function": "Thing.answer"})
    namespace = {}
    exec(mutant, namespace)
    assert namespace["Thing"]().answer() is None
    assert namespace["answer"]() == 42


def test_noop_body_replacement_is_refused():
    with pytest.raises(ValueError, match="change"):
        runner.mutate("def empty():\n    return None\n",
                      {"id": "body", "kind": "return_none", "function": "empty"})


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan")])
def test_invalid_timeouts_are_refused(project, timeout):
    with pytest.raises(ValueError, match="timeout"):
        run(project, timeout=timeout)


def test_source_without_final_newline_has_a_readable_diff(project):
    (project / "subject.py").write_text("def answer(): return 42")
    (project / "check.py").write_text("from subject import answer\nassert answer() == 42\n")
    report = run(project, [MUTATIONS[2]])
    assert report["verdict"] == "PASS"
    assert "-def answer(): return 42\n\\ No newline at end of file\n" in report["mutants"][0]["diff"]


@pytest.mark.parametrize("survivor,exitcode,verdict", [(False, 0, "PASS"), (True, 1, "FAIL")])
def test_cli_reports_each_diff_command_status_and_one_final_verdict(project, survivor, exitcode, verdict):
    mutations = project / "mutations.json"
    mutations.write_text(json.dumps(MUTATIONS + ([SURVIVOR] if survivor else [])))
    result = subprocess.run([sys.executable, runner.__file__, "--root", str(project),
                             "--file", "subject.py", "--mutations", str(mutations),
                             "--", sys.executable, "check.py"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == exitcode, result.stdout + result.stderr
    assert result.stdout.count("VERDICT ") == 1
    assert result.stdout.splitlines()[-1].startswith(f"VERDICT {verdict}")
    for row in MUTATIONS:
        assert f"KILLED {row['id']}" in result.stdout
    if survivor:
        assert "SURVIVED empty-untested" in result.stdout
    assert "COMMAND " in result.stdout and "--- a/subject.py" in result.stdout
