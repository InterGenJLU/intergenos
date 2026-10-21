# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Steam launch wrapper's Python never imports from the directory Steam is
started in, and the installer's payload gate refuses a file list it could not
read.

The launch wrapper (/usr/bin/steam, written by build.sh's WRAPEOF heredoc)
reads its list of required 32-bit libraries with a one-line Python program
given on the command line. A program given with -c has the working directory
first on its import path, so a json.py in the directory Steam was started
from would be imported in place of the standard library's. The wrapper runs
it isolated (python3 -I -c): neither the working directory, the user's site
directory nor a PYTHON* variable reaches the import path. The same rule is held
over every script a recipe writes into the package (a heredoc whose opener
writes under DESTDIR, in any packages/*/*/build.sh): each Python call in it
carries -I, but for the calls named in KNOWN_UNISOLATED.

The installer (/usr/bin/igos-install-steam, the HELPEREOF heredoc) refuses a
release that ships files outside the roots it installs and records. Its file
list came from tar tf piped straight into the filters, and a pipeline's status
is its last command's, so a listing that failed part-way reached the filters
as a short list and the gate read it as nothing unexpected. The listing is
read whole and its own exit status checked before any filter runs.

Once the installer has begun the helper library's record of the run, a
refusal no longer leaves the machine as it was: a first run has written the
acceptance record, and the library's exit trap writes a note of the stopped
attempt for pkm. Each refusal from that point says nothing was installed and
names what the run leaves.

Each case reads the shell it exercises from build.sh in the tree under test,
so what is tested is the text that ships.
"""

import collections
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
BUILD_SH = PACKAGE_DIR / "build.sh"
REPO_ROOT = PACKAGE_DIR.parent.parent.parent

# The planted module notes that it was imported, and by which program, then
# hands the importer the standard library's module, so the wrapper's read
# completes and its own output can be checked as well.
PLANTED = '''
import os as _os, sys as _sys
_here = _os.path.dirname(_os.path.realpath(__file__))
with open(_os.path.join(_here, "PLANTED-MARKER"), "a") as _marker:
    _marker.write(__name__ + " imported by " + " ".join(_sys.argv[:2]) + "\\n")
_name = __name__
del _sys.modules[_name]
_saved = list(_sys.path)
_sys.path[:] = [p for p in _sys.path if _os.path.realpath(p or _os.curdir) != _here]
import importlib as _importlib
_real = _importlib.import_module(_name)
_sys.path[:] = _saved
_sys.modules[_name] = _real
'''

# A Python interpreter named as a command word: python, python3 or python3.14,
# with or without a directory in front of it.
INTERPRETER = re.compile(r"(?<![\w./-])(?:/[\w./-]*/)?python(?:3(?:\.\d+)?)?(?![\w.-])")
# A short option that takes an argument ends its option cluster.
TAKES_AN_ARGUMENT = set("cmWXQ")
# A heredoc opener, any delimiter: bare, quoted, double-quoted or quoted with a
# backslash. An opener that writes the body into a file names the path's leading
# variable after a redirect of standard output (> or >>, anywhere on the opener's
# logical line, before the opener or after it), after install ... /dev/stdin, or
# after tee.
HEREDOC = re.compile(r"<<(-?)\s*(\\?)(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\3")
WRITER = re.compile(r"(?:(?<![<>&0-9])>>?\s*|\binstall\b[^<]*?/dev/stdin\s+|\btee\s+(?:-a\s+)?)[\"']?"
                    r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
# A variable assigned from another, the value starting with it.
ASSIGNED_FROM = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=[\"']?\$\{?([A-Za-z_][A-Za-z0-9_]*)")

# The shipped scripts that run Python without -I today, found when this check
# was widened to every recipe, each left for a change of its own with its own
# proof. None is a python3 -c or python3 - call: each runs a module under -P or
# a script by path, so the working directory is not on its import path, while
# PYTHONPATH and the user's site directory still are. Keyed by recipe and line
# text, with the number of calls that carry that text in that recipe. The calls
# found are counted per key and compared with the count both ways: one more
# call with an excepted line's text fails the check, and so does a key with
# fewer calls than its count, so a fixed call leaves this table with it.
KNOWN_UNISOLATED = {
    ("packages/ai/intergen/build.sh", "#!/usr/bin/env python3"): 2,
    ("packages/core/pkm/build.sh", 'exec /usr/bin/python3 -P -m pkm "$@"'): 1,
    ("packages/desktop/forge/build.sh", 'exec /usr/bin/python3 -P -m installer "$@"'): 1,
    ("packages/desktop/intergen-toggle/build.sh",
     'exec python3 /usr/libexec/intergen-toggle/intergen-toggle.py "$@"'): 1,
    ("packages/desktop/intergen-welcome/build.sh",
     'python3 /usr/libexec/intergen-welcome/intergen-welcome.py "$@"'): 1,
    ("packages/desktop/intergenos-backup/build.sh", 'exec python3 /usr/libexec/chronicle/chronicle-cli "$@"'): 1,
    ("packages/desktop/intergenos-backup/build.sh", 'exec python3 /usr/libexec/chronicle/chronicle-gui "$@"'): 1,
    ("packages/desktop/intergenos-backup/build.sh", 'exec python3 /usr/libexec/chronicle/chronicled "$@"'): 1,
}

# Lines in a shipped file that nothing runs, where a word naming the interpreter
# is text and not a command: maturin's .dist-info METADATA, read as package
# metadata. Keyed and counted as KNOWN_UNISOLATED is and compared with the
# calls found in the same way, so another such line fails the check until it
# is read and named here.
NOT_A_CALL = {
    ("packages/core/maturin/build.sh",
     "Summary: Build and publish crates with pyo3, cffi and uniffi bindings as well as rust binaries as python "
     "packages"): 1,
}


def _heredoc(delimiter):
    """The body of the build.sh heredoc closed by DELIMITER, as (line number, text) pairs."""
    lines = BUILD_SH.read_text(encoding="utf-8").split("\n")
    opener = re.compile(r"<<\s*'" + re.escape(delimiter) + r"'\s*$")
    starts = [i for i, line in enumerate(lines) if opener.search(line)]
    if len(starts) != 1:
        raise AssertionError(f"build.sh opens the {delimiter} heredoc {len(starts)} times, not once")
    body = []
    for i in range(starts[0] + 1, len(lines)):
        if lines[i] == delimiter:
            return body
        body.append((i + 1, lines[i]))
    raise AssertionError(f"build.sh never closes the {delimiter} heredoc")


def _python_calls(body):
    """Every code line of a heredoc body that names a Python interpreter, with the words after the name.

    Comments are skipped, but a first line that is a #! naming Python counts:
    the body is then a Python program, and that line is how it is started."""
    calls = []
    for number, text in body:
        shebang = number == body[0][0] and text.startswith("#!")
        if text.lstrip().startswith("#") and not shebang:
            continue
        for match in INTERPRETER.finditer(text):
            calls.append((number, text, text[match.end():].split()))
    return calls


def _shipped_bodies(root):
    """Every heredoc body that a recipe under ROOT writes into its package, as
    (recipe, delimiter, body): its opener's logical line, read across backslash
    continuations before and after the opener, writes into a path under
    DESTDIR, named directly or through a chain of variables the same build.sh
    assigns from it."""
    found = []
    for build in sorted(root.glob("packages/*/*/build.sh")):
        rel = str(build.relative_to(root))
        text = build.read_text(encoding="utf-8")
        assignments = ASSIGNED_FROM.findall(text)
        from_destdir = {"DESTDIR"}
        while True:
            more = {name for name, source in assignments if source in from_destdir} - from_destdir
            if not more:
                break
            from_destdir |= more
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            opener = HEREDOC.search(lines[i])
            if lines[i].lstrip().startswith("#") or "<<<" in lines[i] or not opener:
                i += 1
                continue
            dash, _, _, delimiter = opener.groups()
            # The opener's logical line may begin on an earlier line and go on
            # past this one, joined by backslash continuations; the body starts
            # after its last line.
            start, end = i, i
            while start > 0 and lines[start - 1].rstrip().endswith("\\"):
                start -= 1
            while end + 1 < len(lines) and lines[end].rstrip().endswith("\\"):
                end += 1
            body, j = [], end + 1
            while j < len(lines) and (lines[j].lstrip("\t") if dash else lines[j]) != delimiter:
                body.append((j + 1, lines[j]))
                j += 1
            if j == len(lines):
                raise AssertionError(f"{rel}:{i + 1}: the {delimiter} heredoc is never closed")
            writer = WRITER.search(" ".join(line.rstrip().rstrip("\\") for line in lines[start:end + 1]))
            if writer and writer.group(1) in from_destdir and body:
                found.append((rel, delimiter, body))
            i = j + 1
    return found


def _unisolated(root):
    """Every Python call in a shipped heredoc body under ROOT without -I, as (recipe, line number, line text)."""
    return [(rel, number, line.strip())
            for rel, _, body in _shipped_bodies(root)
            for number, line, words in _python_calls(body) if not _isolated(words)]


def _against_the_table(found, table):
    """The keys where the calls FOUND and the counts in TABLE disagree, as two
    lists of (recipe, line text, calls found, count named): the keys with more
    calls than the table names, then the keys with fewer."""
    counts = collections.Counter((rel, line) for rel, _, line in found)
    more = [(rel, line, n, table.get((rel, line), 0))
            for (rel, line), n in sorted(counts.items()) if n > table.get((rel, line), 0)]
    fewer = [(rel, line, counts[(rel, line)], n)
             for (rel, line), n in sorted(table.items()) if counts[(rel, line)] < n]
    return more, fewer


def _isolated(words):
    """True when the options in front of the program include -I."""
    for word in words:
        if word == "-" or not word.startswith("-") or word.startswith("--"):
            return False
        for letter in word[1:]:
            if letter == "I":
                return True
            if letter in TAKES_AN_ARGUMENT:
                return False
    return False


def _gate():
    """The installer's payload-completeness gate, from its comment up to the install step."""
    body = _heredoc("HELPEREOF")
    texts = [text for _, text in body]
    start = [i for i, text in enumerate(texts) if text.startswith("# Pre-flight payload-completeness gate")]
    end = [i for i, text in enumerate(texts)
           if text.startswith('echo "  Installing the Steam bootstrap under /usr/lib/steam')]
    if len(start) != 1 or len(end) != 1 or end[0] <= start[0]:
        raise AssertionError(f"the gate's bounds in the installer: start {start}, end {end}")
    return body[start[0]][0], "\n".join(texts[start[0]:end[0]]) + "\n"


def _payload(tmp, name, unexpected):
    """A data.tar.xz in the shape of Valve's: every root the gate knows, the
    directory entries, and one member large and random enough that cutting the
    archive in half lands inside it. A file outside every root, when asked
    for, comes last, after the large member."""
    files = {
        "usr/bin/steamdeps": b"#!/usr/bin/python3\n",
        "usr/lib/steam/bin_steam.sh": b"#!/bin/bash\n",
        "usr/lib/steam/bootstraplinux_ubuntu12_32.tar.xz": random.Random(273).randbytes(512 * 1024),
        "usr/share/applications/steam.desktop": b"[Desktop Entry]\n",
        "lib/udev/rules.d/60-steam-input.rules": b"# rules\n",
        "etc/apt/sources.list.d/steam-stable.list": b"# sources\n",
    }
    if unexpected:
        files["opt/valve/unexpected-tool"] = b"#!/bin/sh\n"
    root = tmp / f"{name}-root"
    members = ["."]
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        parts = Path(rel).parts
        for i in range(1, len(parts)):
            directory = "./" + "/".join(parts[:i])
            if directory not in members:
                members.append(directory)
        members.append("./" + rel)
    # Valve's own /usr/bin/steam, the path the wrapper owns, is a symbolic link.
    (root / "usr/bin/steam").symlink_to("../lib/steam/bin_steam.sh")
    members.insert(members.index("./usr/bin/steamdeps"), "./usr/bin/steam")
    member_list = tmp / f"{name}-members"
    member_list.write_text("\n".join(members) + "\n", encoding="utf-8")
    archive = tmp / f"{name}.tar.xz"
    subprocess.run(["tar", "-cJf", str(archive), "-C", str(root), "--no-recursion", "--owner=0", "--group=0",
                    "--numeric-owner", "--mtime=@0", "-T", str(member_list)], check=True, timeout=60)
    return archive


def _run_gate(gate, archive, directory):
    """The gate's shell under the installer's own set -e, in a directory holding ARCHIVE as data.tar.xz, after the
    installer's own assignments of the acceptance record's path, which its refusals name."""
    directory.mkdir()
    shutil.copyfile(archive, directory / "data.tar.xz")
    paths = [text for _, text in _heredoc("HELPEREOF") if text.startswith(("ACCEPTANCE_DIR=", "ACCEPTANCE_FILE="))]
    if len(paths) != 2:
        raise AssertionError(f"the installer's acceptance paths: {paths}")
    env = {k: v for k, v in os.environ.items() if k != "IGOS_HELPER_MANIFEST_DIR"}
    return subprocess.run(["/bin/bash", "-c", "set -e\n" + "\n".join(paths) + "\n" + gate + 'echo "GATE PASSED"\n'],
                          cwd=directory, env=env, capture_output=True, text=True, timeout=60)


class LaunchWrapperPythonTests(unittest.TestCase):
    def setUp(self):
        # Neither variable may decide the reading: PYTHONSAFEPATH would keep the
        # working directory off the path without -I, PYTHONPATH would add one.
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}

    def test_the_manifest_read_never_imports_a_module_from_the_launch_directory(self):
        calls = _python_calls(_heredoc("WRAPEOF"))
        # A zero here would prove nothing: the wrapper reads its manifest with Python.
        self.assertEqual(len(calls), 1, f"the wrapper should call Python once; found {calls}")
        number, line, _ = calls[0]
        self.assertTrue(line.startswith("sonames=$("), f"build.sh:{number} is not the manifest read: {line}")
        manifest_text = "\n".join(text for _, text in _heredoc("JSONEOF")) + "\n"
        expected = json.loads(manifest_text)["sonames"]
        self.assertGreaterEqual(len(expected), 20)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manifest = tmp / "closure.json"
            manifest.write_text(manifest_text, encoding="utf-8")
            control, planted = tmp / "control", tmp / "planted"
            for directory in (control, planted):
                directory.mkdir()
                (directory / "json.py").write_text(PLANTED, encoding="utf-8")
            # The plant is live: a program given with -c and no -I imports it.
            subprocess.run(["python3", "-c", "import json"], cwd=control, env=self.env, check=True, timeout=60)
            self.assertTrue((control / "PLANTED-MARKER").exists(),
                            "the planted module is not reachable through the working directory; "
                            "this case would prove nothing")
            result = subprocess.run(
                ["/bin/bash", "-c", f'MANIFEST="$1"\n{line}\nprintf "%s\\n" "$sonames"\n', "_", str(manifest)],
                cwd=planted, env=self.env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.split(), expected, result.stderr)
            marker = planted / "PLANTED-MARKER"
            self.assertFalse(marker.exists(), f"build.sh:{number} imported the planted module: "
                                              f"{marker.read_text() if marker.exists() else ''}")

    def test_every_python_call_in_the_shipped_scripts_runs_isolated(self):
        bodies = _shipped_bodies(REPO_ROOT)
        read = {(rel, delimiter) for rel, delimiter, _ in bodies}
        steam = str(BUILD_SH.relative_to(REPO_ROOT))
        # The check reads every recipe's shipped scripts, this package's two among them.
        for delimiter in ("WRAPEOF", "HELPEREOF"):
            self.assertIn((steam, delimiter), read, f"the {delimiter} script is not among the bodies read")
            self.assertEqual(_heredoc(delimiter)[0][1], "#!/bin/bash",
                             f"the {delimiter} script is not the shell script this case reads")
        self.assertGreater(len({rel for rel, _ in read}), 1, "only one recipe's scripts were read")
        # A zero here would prove nothing: the wrapper carries one call.
        wrapper_calls = _python_calls(_heredoc("WRAPEOF"))
        self.assertEqual(len(wrapper_calls), 1, "the wrapper's Python call is not found; this case has stopped "
                                                "reading it")
        # The check finds a call planted in a copy of another recipe, and passes the
        # same call once it carries -I.
        other = sorted(rel for rel, delimiter, body in bodies
                       if rel != steam and any(line == "source /usr/share/igos/helpers/helper-lib.sh"
                                               for _, line in body))[0]
        original = (REPO_ROOT / other).read_text(encoding="utf-8")
        self.assertEqual(original.count("\nsource /usr/share/igos/helpers/helper-lib.sh\n"), 1)
        for call, expected in (('python3 -c "import json"', 1), ('python3 -I -c "import json"', 0)):
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / other
                copy.parent.mkdir(parents=True)
                copy.write_text(original.replace("\nsource /usr/share/igos/helpers/helper-lib.sh\n",
                                                 f"\nsource /usr/share/igos/helpers/helper-lib.sh\n{call}\n"),
                                encoding="utf-8")
                planted = [hit for hit in _unisolated(Path(tmp)) if hit[2] == call]
                self.assertEqual(len(planted), expected, f"{call} planted in a copy of {other}: found {planted}")
        # A script is read whatever the shape of the opener that writes it.
        script = "#!/bin/sh\npython3 -c \"import sys\"\nPLANTEOF\n"
        for shape, opener in (
                ("an opener continued over two lines",
                 'install -Dm755 /dev/stdin \\\n    "${DESTDIR}/usr/bin/planted-tool" <<\'PLANTEOF\''),
                ("the redirect after the opener", 'cat << \'PLANTEOF\' > "${DESTDIR}/usr/bin/planted-tool"'),
                ("the redirect on a line continuing the opener",
                 'cat << \'PLANTEOF\' \\\n    > "${DESTDIR}/usr/bin/planted-tool"'),
                ("a destination two assignments from DESTDIR",
                 'planted_root="${DESTDIR}/usr"\nplanted_bin="${planted_root}/bin"\n'
                 'cat > "${planted_bin}/planted-tool" <<\'PLANTEOF\''),
                ("a delimiter quoted with a backslash", 'cat > "${DESTDIR}/usr/bin/planted-tool" <<\\PLANTEOF')):
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / other
                copy.parent.mkdir(parents=True)
                copy.write_text(f"{original}\n{opener}\n{script}", encoding="utf-8")
                planted = [hit for hit in _unisolated(Path(tmp)) if hit[2] == 'python3 -c "import sys"']
                self.assertEqual(len(planted), 1, f"a call in a script written by {shape}, planted in a copy of "
                                                  f"{other}: found {planted}")
        found = _unisolated(REPO_ROOT)
        more, fewer = _against_the_table(found, KNOWN_UNISOLATED | NOT_A_CALL)

        def lines_of(rel, line):
            return ", ".join(str(number) for r, number, text in found if (r, text) == (rel, line))

        self.assertEqual(more, [], "a shipped script runs Python without -I: " + "; ".join(
            f"{rel}: {line} ({n} calls, at lines {lines_of(rel, line)}; {count} named as known)"
            for rel, line, n, count in more))
        self.assertEqual(fewer, [], "fewer calls than the table names, so its count no longer holds: " + "; ".join(
            f"{rel}: {line} ({n} calls found, {count} named)" for rel, line, n, count in fewer))

        # The count per key holds both ways: one more call with an excepted
        # line's text, in that exception's recipe, is found, and so is one fewer.
        intergen, shebang = "packages/ai/intergen/build.sh", "#!/usr/bin/env python3"
        named = KNOWN_UNISOLATED[(intergen, shebang)]
        text = (REPO_ROOT / intergen).read_text(encoding="utf-8")
        self.assertEqual(text.count(f"\n{shebang}\n"), named)
        table = {key: n for key, n in KNOWN_UNISOLATED.items() if key[0] == intergen}
        for label, planted, expected in (
                ("one more", text + '\ninstall -Dm755 /dev/stdin "${DESTDIR}/usr/bin/planted-tool" '
                                    f"<<'PLANTEOF'\n{shebang}\nprint(\"planted\")\nPLANTEOF\n",
                 ([(intergen, shebang, named + 1, named)], [])),
                ("one fewer", text.replace(f"\n{shebang}\n", "\n#!/usr/bin/python3 -I\n", 1),
                 ([], [(intergen, shebang, named - 1, named)]))):
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / intergen
                copy.parent.mkdir(parents=True)
                copy.write_text(planted, encoding="utf-8")
                self.assertEqual(_against_the_table(_unisolated(Path(tmp)), table), expected,
                                 f"a copy of {intergen} with {label} call carrying the text {shebang!r}")


class PayloadListingGateTests(unittest.TestCase):
    def test_a_listing_that_fails_part_way_is_refused_not_read_as_nothing_unexpected(self):
        number, gate = _gate()
        self.assertIn("tar tf data.tar.xz", gate, f"build.sh:{number}: the gate no longer lists the archive")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            good = _payload(tmp, "good", unexpected=False)
            unexpected = _payload(tmp, "unexpected", unexpected=True)
            truncated = tmp / "truncated.tar.xz"
            data = unexpected.read_bytes()
            truncated.write_bytes(data[:len(data) // 2])
            # The archives are what this case says they are: the whole one lists
            # the unexpected file; the cut one fails part-way, before reaching it.
            whole = subprocess.run(["tar", "tf", str(unexpected)], capture_output=True, text=True, timeout=60)
            self.assertEqual(whole.returncode, 0, whole.stderr)
            self.assertIn("./opt/valve/unexpected-tool", whole.stdout.split())
            cut = subprocess.run(["tar", "tf", str(truncated)], capture_output=True, text=True, timeout=60)
            self.assertNotEqual(cut.returncode, 0, "the cut archive lists cleanly; this case would prove nothing")
            self.assertNotIn("./opt/valve/unexpected-tool", cut.stdout.split())
            # The gate still passes a release it handles and refuses one it does not.
            passed = _run_gate(gate, good, tmp / "run-good")
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertIn("GATE PASSED", passed.stdout)
            refused = _run_gate(gate, unexpected, tmp / "run-unexpected")
            self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
            self.assertIn("opt/valve/unexpected-tool", refused.stdout)
            # The reading: a listing that failed part-way, short of the unexpected file.
            result = _run_gate(gate, truncated, tmp / "run-truncated")
            self.assertNotIn("GATE PASSED", result.stdout,
                             f"build.sh:{number}: a listing that failed part-way passed the gate as nothing "
                             f"unexpected; stderr: {result.stderr}")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("ERROR:", result.stdout)
            # It says nothing was installed, and names what the stopped run leaves.
            said = re.sub(r"\s+", " ", result.stdout)
            self.assertIn("Nothing was installed, and nothing on this machine was changed beyond", said)
            for left in ("/var/lib/intergen/legal/steam-1.0-accepted.json",
                         "/var/lib/igos/helpers/steam.manifest.partial", "working files under /tmp"):
                self.assertIn(left, said)


class InstallerRefusalTests(unittest.TestCase):
    def test_every_refusal_after_the_record_begins_names_what_the_run_leaves(self):
        body = _heredoc("HELPEREOF")
        texts = [text for _, text in body]
        begins = [i for i, text in enumerate(texts) if text.startswith('igos_helper_init "steam"')]
        self.assertEqual(len(begins), 1, f"the installer begins its record {len(begins)} times, not once")
        refusals = [i for i, text in enumerate(texts) if i > begins[0] and text.lstrip().startswith('echo "  ERROR:')]
        # A zero here would prove nothing: the installer refuses six ways once its record has begun (the
        # lookup, the download, the signature, the unpacking, the listing and the payload's paths).
        self.assertEqual(len(refusals), 6, f"refusals after the record begins, at build.sh lines "
                                           f"{[body[i][0] for i in refusals]}")
        leaves = ("nothing on this machine was changed beyond the record of your acceptance of the agreement "
                  "($ACCEPTANCE_FILE), a note of this stopped attempt for pkm "
                  "(${IGOS_HELPER_MANIFEST_DIR:-/var/lib/igos/helpers}/steam.manifest.partial) and the run's "
                  "working files under /tmp.")
        for i in refusals:
            end = next(j for j in range(i, len(texts)) if texts[j].strip().startswith("exit "))
            said = re.sub(r"\s+", " ", " ".join(re.sub(r'^\s*echo "(.*)"$', r"\1", text) for text in texts[i:end]))
            self.assertRegex(said, r"(?i)\b(nothing was|was not) installed\b",
                             f"build.sh:{body[i][0]}: the refusal does not say nothing was installed: {said}")
            self.assertIn(leaves, said, f"build.sh:{body[i][0]}: the refusal does not name what the run leaves")


if __name__ == "__main__":
    unittest.main()
