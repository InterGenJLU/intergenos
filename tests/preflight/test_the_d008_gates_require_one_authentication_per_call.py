# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The D-008 gates require one administrator authentication per privileged call.

scripts/check-d008-compliance.sh (Gate G, the source tree) and
scripts/check-d008-runtime.sh (Gate A, the assembled chroot) both run
scripts/lib/check-privileged-runner-policy.py, which parses the PolicyKit policy
per action: every action bound to the privileged runner must be auth_admin in
allow_any, allow_inactive and allow_active (no keep), carry its own exec.argv1,
and the argv1 set must equal _PRIVILEGED_TOOLS.

Until 2026-10-22 both gates did the opposite: they passed when the string
auth_admin_keep appeared anywhere in the policy and called that per-action
authentication. A kept authorization covers every runner for five minutes,
because every runner has the same parent (the person's systemd user manager)
and pkexec asks PolicyKit about that parent.

The REAL gate scripts run here, against a scratch copy of exactly the files they
read, with the tree's own policy and with planted ones. The positive control is
the planted keep: a gate that cannot refuse it has not been shown to check
anything.

The checker reads the policy as polkit 127 reads it, because PolicyKit enforces
the values IT reads: a repeated element's LAST value, a default's text taken
exactly (any other text stops PolicyKit reading the file), an annotation's text
untrimmed, an unknown element skipped with all it holds. Until 2026-10-22 it
judged the FIRST <defaults> and <allow_*> and trimmed the text, so a repeated
<allow_active> whose second value was a keep passed both gates. Each element
PolicyKit reads once is now refused when it appears twice, naming the action and
the element, and so is a file PolicyKit would stop reading.

The allowlist is read as the dispatcher holds it. The dispatcher imports
_PRIVILEGED_TOOLS, so it holds the value Python bound last; until 2026-10-22 the
checker took the first assignment its walk of the syntax tree met, so a
reassignment, a definition only inside a function or one in a branch Python
does not run passed against a set the dispatcher does not hold. Only a single
unconditional module-level definition is read now; every other binding of the
name is refused, naming its line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "lib" / "check-privileged-runner-policy.py"
POLICY = REPO_ROOT / "intergen" / "data" / "org.intergenos.intergen.policy"
REGISTRY = REPO_ROOT / "intergen" / "tool_registry.py"

#: The files check-d008-compliance.sh reads, relative to the repository root.
COMPLIANCE_READS = (
    "intergen/interfaces/provenance.py",
    "intergen/provenance.py",
    "intergen/tool_registry.py",
    "intergen/review_modal.py",
    "intergen/audit_log.py",
    "intergen/data/intergen-privileged-runner",
    "intergen/data/org.intergenos.intergen.policy",
    "intergen/interfaces/types.py",
)

#: The deployed paths check-d008-runtime.sh reads, and their sources in the tree.
PYROOT = "usr/lib/python3.14/site-packages/intergen"
RUNTIME_READS = {
    f"{PYROOT}/interfaces/provenance.py": "intergen/interfaces/provenance.py",
    f"{PYROOT}/provenance.py": "intergen/provenance.py",
    f"{PYROOT}/tool_registry.py": "intergen/tool_registry.py",
    f"{PYROOT}/interfaces/types.py": "intergen/interfaces/types.py",
    f"{PYROOT}/review_modal.py": "intergen/review_modal.py",
    f"{PYROOT}/audit_log.py": "intergen/audit_log.py",
    "usr/share/polkit-1/actions/org.intergenos.intergen.policy":
        "intergen/data/org.intergenos.intergen.policy",
    "usr/bin/intergen-privileged-runner": "intergen/data/intergen-privileged-runner",
}


def _with_a_keep(text):
    """The policy with ONE runner-bound action's allow_active turned to a keep."""
    marker = "<allow_active>auth_admin</allow_active>"
    assert marker in text, "the policy carries no auth_admin allow_active to plant a keep into"
    return text.replace(marker, "<allow_active>auth_admin_keep</allow_active>", 1)


def _checker(policy, registry=REGISTRY):
    return subprocess.run(
        ["python3", "-I", str(CHECKER), "--policy", str(policy), "--registry", str(registry)],
        capture_output=True, text=True, check=False, timeout=60,
    )


def _compliance_tree(tmp_path, policy_text):
    root = tmp_path / "tree"
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for rel in COMPLIANCE_READS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, root / rel)
    (root / "packages" / "ai" / "intergen").mkdir(parents=True)
    (root / "intergen" / "data" / "org.intergenos.intergen.policy").write_text(policy_text)
    return root


def _run_compliance(root):
    return subprocess.run(
        ["bash", str(root / "scripts" / "check-d008-compliance.sh")],
        capture_output=True, text=True, check=False, timeout=120,
    )


def _runtime_root(tmp_path, policy_text):
    root = tmp_path / "chroot"
    for dest, source in RUNTIME_READS.items():
        (root / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / source, root / dest)
    (root / "usr/share/polkit-1/actions/org.intergenos.intergen.policy").write_text(policy_text)
    (root / "usr/bin/intergen").write_text("#!/bin/sh\n")
    os.chmod(root / "usr/bin/intergen", 0o755)
    os.chmod(root / "usr/bin/intergen-privileged-runner", 0o755)
    (root / "etc/logrotate.d").mkdir(parents=True)
    (root / "etc/logrotate.d/intergen-tool-dispatch").write_text("# rotation\n")
    return root


def _run_runtime(root):
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "check-d008-runtime.sh"), str(root)],
        capture_output=True, text=True, check=False, timeout=120,
    )


def test_the_checker_passes_the_shipped_policy():
    completed = _checker(POLICY)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.startswith("PASS: 4 action(s) bound to /usr/bin/intergen-privileged-runner")


def test_the_checker_refuses_a_planted_keep_naming_the_action(tmp_path):
    planted = tmp_path / "keep.policy"
    planted.write_text(_with_a_keep(POLICY.read_text()))
    completed = _checker(planted)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "allow_active is 'auth_admin_keep', not 'auth_admin'" in completed.stdout


def test_the_checker_refuses_a_runner_action_with_no_argv1(tmp_path):
    text = POLICY.read_text()
    line = '    <annotate key="org.freedesktop.policykit.exec.argv1">write_file</annotate>\n'
    assert line in text
    planted = tmp_path / "no-argv1.policy"
    planted.write_text(text.replace(line, "", 1))
    completed = _checker(planted)
    assert completed.returncode == 1, completed.stdout
    assert "0 exec.argv1 annotations" in completed.stdout
    assert "privileged tools with no action of their own: ['write_file']" in completed.stdout


def test_the_checker_reads_actions_not_comments(tmp_path):
    """A keep in a comment is prose: it neither fails a no-keep policy nor
    rescues a keep one. The grep this replaced could be satisfied by either."""
    text = POLICY.read_text().replace(
        "<policyconfig>", "<!-- auth_admin_keep, named in a comment -->\n<policyconfig>", 1)
    planted = tmp_path / "comment.policy"
    planted.write_text(text)
    assert _checker(planted).returncode == 0


def test_the_checker_cannot_pass_a_policy_that_binds_nothing(tmp_path):
    planted = tmp_path / "empty.policy"
    planted.write_text('<?xml version="1.0"?>\n<policyconfig></policyconfig>\n')
    completed = _checker(planted)
    assert completed.returncode == 1
    assert "nothing was checked" in completed.stdout


def test_the_compliance_gate_passes_the_tree_and_refuses_a_planted_keep(tmp_path):
    clean = _run_compliance(_compliance_tree(tmp_path / "clean", POLICY.read_text()))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "PASS — every action bound to the privileged runner is auth_admin with no keep" in clean.stdout

    keep = _run_compliance(_compliance_tree(tmp_path / "keep", _with_a_keep(POLICY.read_text())))
    assert keep.returncode == 1, keep.stdout + keep.stderr
    assert ("VIOLATION: the PolicyKit policy does not require one administrator "
            "authentication per privileged call") in keep.stdout


def test_the_runtime_gate_passes_the_tree_and_refuses_a_planted_keep(tmp_path):
    clean = _run_runtime(_runtime_root(tmp_path / "clean", POLICY.read_text()))
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "PASS — PolicyKit policy deployed + structurally valid" in clean.stdout

    keep = _run_runtime(_runtime_root(tmp_path / "keep", _with_a_keep(POLICY.read_text())))
    assert keep.returncode == 1, keep.stdout + keep.stderr
    assert ("VIOLATION: deployed PolicyKit policy does not require one administrator "
            "authentication per privileged call") in keep.stdout


ACTIVE = "<allow_active>auth_admin</allow_active>"
PATH_LINE = ('<annotate key="org.freedesktop.policykit.exec.path">'
             '/usr/bin/intergen-privileged-runner</annotate>')
ARGV1_LINE = '<annotate key="org.freedesktop.policykit.exec.argv1">manage_services</annotate>'
SERVICES = "org.intergenos.intergen.privileged-tool.manage-services"


def _changed(old, new):
    """The shipped policy with its first `old` replaced by `new`."""
    text = POLICY.read_text()
    assert old in text, f"the policy carries no {old!r} to change"
    return text.replace(old, new, 1)


def _with_a_repeated_keep():
    """The reader's control: the first auth_admin allow_active followed by a keep."""
    return _changed(ACTIVE, ACTIVE + "\n      <allow_active>auth_admin_keep</allow_active>")


def _first_action():
    text = POLICY.read_text()
    start = text.index(f'<action id="{SERVICES}">')
    return text[start:text.index("</action>", start) + len("</action>")]


def _planted(tmp_path, name, text):
    path = tmp_path / f"{name}.policy"
    path.write_text(text)
    return path


def test_the_checker_refuses_a_repeated_allow_active_naming_the_action_and_element(tmp_path):
    """The repeated keep passed as no keep: PolicyKit keeps the last value."""
    completed = _checker(_planted(tmp_path, "repeated", _with_a_repeated_keep()))
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert f"{SERVICES}: <allow_active> appears 2 times" in completed.stdout
    assert f"{SERVICES}: allow_active is 'auth_admin_keep', not 'auth_admin'" in completed.stdout


#: Every element PolicyKit reads once per action or per file, repeated with a
#: different value: (the change, what the refusal names).
REPEATS = {
    "allow_any": (("<allow_any>auth_admin</allow_any>",
                   "<allow_any>auth_admin</allow_any><allow_any>yes</allow_any>"),
                  f"{SERVICES}: <allow_any> appears 2 times"),
    "allow_inactive": (("<allow_inactive>auth_admin</allow_inactive>",
                        "<allow_inactive>auth_admin</allow_inactive><allow_inactive>yes</allow_inactive>"),
                       f"{SERVICES}: <allow_inactive> appears 2 times"),
    "defaults": (("</defaults>", "</defaults><defaults><allow_active>auth_admin_keep</allow_active></defaults>"),
                 f"{SERVICES}: <defaults> appears 2 times"),
    "exec.path": ((PATH_LINE, PATH_LINE + PATH_LINE.replace("intergen-privileged-runner", "true")),
                  f'{SERVICES}: <annotate key="org.freedesktop.policykit.exec.path"> appears 2 times'),
    "exec.argv1": ((ARGV1_LINE, ARGV1_LINE + ARGV1_LINE.replace("manage_services", "run_command")),
                   f'{SERVICES}: <annotate key="org.freedesktop.policykit.exec.argv1"> appears 2 times'),
    "action vendor": ((ARGV1_LINE, ARGV1_LINE + "<vendor>A</vendor><vendor>B</vendor>"),
                      f"{SERVICES}: <vendor> appears 2 times"),
    "action icon_name": ((ARGV1_LINE, ARGV1_LINE + "<icon_name>a</icon_name><icon_name>b</icon_name>"),
                         f"{SERVICES}: <icon_name> appears 2 times"),
    "file vendor": (("<vendor>InterGenOS</vendor>", "<vendor>InterGenOS</vendor><vendor>Another</vendor>"),
                    "the file: <vendor> appears 2 times"),
    "file icon_name": (("<icon_name>system-software-install</icon_name>",
                        "<icon_name>system-software-install</icon_name><icon_name>dialog-password</icon_name>"),
                       "the file: <icon_name> appears 2 times"),
    "message": (("<message>", "<message>A different prompt</message><message>"),
                f"{SERVICES}: <message> appears 2 times"),
}


@pytest.mark.parametrize("element", sorted(REPEATS))
def test_the_checker_refuses_each_element_polkit_reads_once_when_repeated(tmp_path, element):
    (old, new), named = REPEATS[element]
    completed = _checker(_planted(tmp_path, "repeat", _changed(old, new)))
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert named in completed.stdout, completed.stdout
    assert "PolicyKit keeps the last" in completed.stdout


def test_the_checker_refuses_a_duplicated_action(tmp_path):
    """PolicyKit holds the LAST action of an id; here that copy is a yes."""
    copy = _first_action().replace(ACTIVE, "<allow_active>yes</allow_active>")
    completed = _checker(_planted(tmp_path, "dup", _changed("</policyconfig>", copy + "</policyconfig>")))
    assert completed.returncode == 1, completed.stdout
    assert f"{SERVICES}: <action> appears 2 times" in completed.stdout
    assert f"{SERVICES}: allow_active is 'yes', not 'auth_admin'" in completed.stdout


@pytest.mark.parametrize("label,old,new", [
    ("a padded default", ACTIVE, "<allow_active> auth_admin </allow_active>"),
    ("a default across two lines", ACTIVE, "<allow_active>auth_admin\n</allow_active>"),
    ("an annotate with a second attribute", ARGV1_LINE,
     ARGV1_LINE.replace('exec.argv1"', 'exec.argv1" lang="x"')),
])
def test_the_checker_refuses_a_file_polkit_stops_reading(tmp_path, label, old, new):
    """The old checker trimmed these and passed them; PolicyKit stops reading the
    file there, so the actions from that point on do not exist for it."""
    completed = _checker(_planted(tmp_path, "stop", _changed(old, new)))
    assert completed.returncode == 1, f"{label}: {completed.stdout}"
    assert "PolicyKit stops reading this file at line" in completed.stdout


def test_the_checker_reads_a_padded_exec_path_as_polkit_does(tmp_path):
    """PolicyKit keeps an annotation's spaces, so a padded path binds nothing."""
    text = POLICY.read_text().replace(
        ">/usr/bin/intergen-privileged-runner<", "> /usr/bin/intergen-privileged-runner <")
    completed = _checker(_planted(tmp_path, "padded", text))
    assert completed.returncode == 1, completed.stdout
    assert "no action is bound to /usr/bin/intergen-privileged-runner" in completed.stdout


def test_the_checker_skips_an_action_polkit_skips(tmp_path):
    """An action inside an element PolicyKit does not know is skipped with all it
    holds, by PolicyKit and by the checker alike: its yes is never enforced."""
    nested = _first_action().replace(SERVICES, SERVICES + "-nested").replace(
        ACTIVE, "<allow_active>yes</allow_active>").replace("manage_services", "manage_services_nested")
    completed = _checker(_planted(tmp_path, "nested",
                                  _changed("</policyconfig>", f"<extra>{nested}</extra></policyconfig>")))
    assert completed.returncode == 0, completed.stdout
    assert "-nested" not in completed.stdout


def test_the_exec_paths_are_listed_as_polkit_reads_them(tmp_path):
    """Gate F's reading: every action's exec.path, a comment's never."""
    commented = _planted(tmp_path, "comment", _changed(
        "<policyconfig>", "<!-- " + PATH_LINE.replace("privileged-runner", "evil") + " -->\n<policyconfig>"))
    completed = subprocess.run(["python3", "-I", str(CHECKER), "--exec-paths", "--policy", str(commented)],
                               capture_output=True, text=True, check=False, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    paths = [line.split("\t")[1] for line in completed.stdout.splitlines()]
    assert paths.count("/usr/bin/intergen-privileged-runner") == 4, completed.stdout
    assert "/usr/bin/intergen-evil" not in completed.stdout


def test_both_gates_refuse_the_repeated_keep_at_a_real_firing(tmp_path):
    compliance = _run_compliance(_compliance_tree(tmp_path / "source", _with_a_repeated_keep()))
    assert compliance.returncode == 1, compliance.stdout + compliance.stderr
    assert "<allow_active> appears 2 times" in compliance.stdout
    assert ("VIOLATION: the PolicyKit policy does not require one administrator "
            "authentication per privileged call") in compliance.stdout

    runtime = _run_runtime(_runtime_root(tmp_path / "chroot", _with_a_repeated_keep()))
    assert runtime.returncode == 1, runtime.stdout + runtime.stderr
    assert "<allow_active> appears 2 times" in runtime.stdout
    assert ("VIOLATION: deployed PolicyKit policy does not require one administrator "
            "authentication per privileged call") in runtime.stdout


def test_the_runtime_gate_reads_the_exec_path_as_polkit_does(tmp_path):
    """Gate F read the first exec.path a grep met, a comment's included."""
    text = POLICY.read_text().replace(
        ">/usr/bin/intergen-privileged-runner<", "> /usr/bin/intergen-privileged-runner <")
    text = text.replace("<policyconfig>", "<!-- " + PATH_LINE + " -->\n<policyconfig>", 1)
    runtime = _run_runtime(_runtime_root(tmp_path, text))
    assert runtime.returncode == 1, runtime.stdout + runtime.stderr
    assert "VIOLATION: PolicyKit exec.path does not match canonical runner location" in runtime.stdout


DEFINITION = ("_PRIVILEGED_TOOLS = frozenset({'manage_packages', 'manage_services', "
              "'run_command', 'write_file'})")
OTHER = "frozenset({'fixture_other'})"

#: Registries whose module, once Python has run it, holds a _PRIVILEGED_TOOLS
#: other than the first definition's (or none): the checker read that first
#: definition and passed the shipped policy against it.
REGISTRY_FORMS = {
    "a reassignment": f"{DEFINITION}\n_PRIVILEGED_TOOLS = {OTHER}\n",
    "a definition inside a function": f"def unused():\n    {DEFINITION}\n",
    "a definition in a branch": f"if False:\n    {DEFINITION}\nelse:\n    _PRIVILEGED_TOOLS = {OTHER}\n",
    "an augmented assignment": f"{DEFINITION}\n_PRIVILEGED_TOOLS |= {OTHER}\n",
    "a function that rebinds the global": (f"{DEFINITION}\ndef rebind():\n    global _PRIVILEGED_TOOLS\n"
                                           f"    _PRIVILEGED_TOOLS = {OTHER}\nrebind()\n"),
    "a loop target": f"{DEFINITION}\nfor _PRIVILEGED_TOOLS in [{OTHER}]:\n    pass\n",
    "an import of that name": f"{DEFINITION}\nfrom json import loads as _PRIVILEGED_TOOLS\n",
    "a binding by name at run time": f'{DEFINITION}\nglobals()["_PRIVILEGED_TOOLS"] = {OTHER}\n',
    "an exec of a built string": f'{DEFINITION}\nexec("_PRIVILEGED_" + "TOOLS = {OTHER}")\n',
    "a rebound frozenset": ("import builtins\nfrozenset = lambda items: builtins.frozenset({'fixture_other'})\n"
                            f"{DEFINITION}\n"),
}


@pytest.mark.parametrize("form", sorted(REGISTRY_FORMS))
def test_the_checker_refuses_a_registry_python_binds_otherwise(tmp_path, form):
    """The dispatcher imports _PRIVILEGED_TOOLS, so it holds what Python bound
    last; the checker reads only a single unconditional module-level definition."""
    registry = tmp_path / "tool_registry.py"
    registry.write_text(REGISTRY_FORMS[form])
    completed = _checker(POLICY, registry)
    assert completed.returncode == 1, f"{form}: {completed.stdout}{completed.stderr}"
    assert ("_PRIVILEGED_TOOLS is bound" in completed.stdout
            or "frozenset is bound" in completed.stdout), completed.stdout
    assert "PASS" not in completed.stdout


def test_the_refusal_names_each_binding_and_where_it_is(tmp_path):
    registry = tmp_path / "tool_registry.py"
    registry.write_text(REGISTRY_FORMS["a definition in a branch"])
    completed = _checker(POLICY, registry)
    assert ("_PRIVILEGED_TOOLS is bound 2 time(s) (line 2, inside an if, for, while, try, with or match "
            "block: assigned; line 4, inside an if, for, while, try, with or match block: assigned)"
            ) in completed.stdout, completed.stdout


def test_the_checker_reads_the_value_python_binds(tmp_path):
    """A single definition, mentioned in a comment and a docstring besides, is
    the value the module holds when run, and the shipped policy passes on it."""
    registry = tmp_path / "tool_registry.py"
    registry.write_text(f'"""_PRIVILEGED_TOOLS is the allowlist."""\n# _PRIVILEGED_TOOLS = {OTHER}\n'
                        f"{DEFINITION.replace(' =', ': frozenset[str] =', 1)}\n")
    ran = subprocess.run(
        ["python3", "-I", "-c", "import runpy, sys; print(sorted(runpy.run_path(sys.argv[1])['_PRIVILEGED_TOOLS']))",
         str(registry)], capture_output=True, text=True, check=True, timeout=60)
    completed = _checker(POLICY, registry)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    held = ran.stdout.strip().strip("[]").replace("'", "")
    assert completed.stdout.rstrip().endswith(f"_PRIVILEGED_TOOLS ({held})"), (ran.stdout, completed.stdout)


def test_both_gates_refuse_a_reassigned_registry_at_a_real_firing(tmp_path):
    """The tree's registry with the allowlist rebound after its definition: the
    dispatcher would hold the second set, the checker read the first and passed."""
    reassigned = REGISTRY.read_text() + f"\n_PRIVILEGED_TOOLS = {OTHER}\n"
    source = _compliance_tree(tmp_path / "source", POLICY.read_text())
    (source / "intergen" / "tool_registry.py").write_text(reassigned)
    compliance = _run_compliance(source)
    assert compliance.returncode == 1, compliance.stdout + compliance.stderr
    assert "_PRIVILEGED_TOOLS is bound 2 time(s)" in compliance.stdout
    assert ("VIOLATION: the PolicyKit policy does not require one administrator "
            "authentication per privileged call") in compliance.stdout

    chroot = _runtime_root(tmp_path / "chroot", POLICY.read_text())
    (chroot / PYROOT / "tool_registry.py").write_text(reassigned)
    runtime = _run_runtime(chroot)
    assert runtime.returncode == 1, runtime.stdout + runtime.stderr
    assert "_PRIVILEGED_TOOLS is bound 2 time(s)" in runtime.stdout
    assert ("VIOLATION: deployed PolicyKit policy does not require one administrator "
            "authentication per privileged call") in runtime.stdout


@pytest.mark.parametrize("gate", ["check-d008-compliance.sh", "check-d008-runtime.sh"])
def test_no_gate_requires_or_names_the_keep_as_per_action_authentication(gate):
    """The words that certified the keep are gone from both gates' checks."""
    text = (REPO_ROOT / "scripts" / gate).read_text()
    assert 'grep -qE "auth_admin_keep"' not in text and 'grep -q "auth_admin_keep"' not in text
    assert "auth_admin_keep per-action authentication" not in text
    assert "check-privileged-runner-policy.py" in text
