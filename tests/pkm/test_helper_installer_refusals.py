#!/usr/bin/env python3
"""The download helpers' installers say why they stopped.

Each of these installers runs under set -e. A step whose failure has no
refusal of its own stops the installer there without a word of its own: the
reader sees, at most, one line from the program that failed. The lookup in
the vendor's signed listing, the download and the unpacking each reach a
refusal instead, and the download and unpacking refusals say that nothing was
installed and name what the stopped run leaves.

Each case fires the installer's own lines, read from the recipe, from the
lookup up to the install step, under set -e, with the steps they call stood
in for by shell functions: the lookup, the download (the transport, and the
helper library's download function, which hands the fetch to the transport it is
named), the signature check, ar and tar. Nothing is downloaded or installed, and nothing is written outside a
scratch directory.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS = ("brave", "chatgpt", "chrome", "edge", "signal", "spotify", "steam", "vscode")
# The first line of the install step, where the installer starts placing files.
INSTALL_STEP = re.compile(r'^(echo "  Installing|rm -rf /opt/|mkdir -p /opt/|cp -a )')
ASSIGNMENT = re.compile(r"^[A-Z][A-Z0-9_]*=")

FIND_FAILS = 'echo "ERROR: could not read verified vendor metadata" >&2; return 1'
FIND_EMPTY = "return 0"
FIND_OK = 'echo "pkg_1.0_amd64.deb|1.0|pool/main/p/pkg_1.0_amd64.deb"'
WGET_FAILS = "return 4"
WGET_OK = 'local prev="" a; for a in "$@"; do [ "$prev" = -O ] && : > "$a"; prev=$a; done; return 0'
AR_FAILS = "return 1"
AR_OK = ": > data.tar.xz; : > data.tar.gz; : > data.tar.zst; return 0"
AR_OK_NO_DATA = "return 0"
TAR_FAILS = "return 2"
TAR_OK = "mkdir -p usr/lib/chatgpt; return 0"


def _installer(name):
    """The installer the recipe writes (its HELPEREOF heredoc), as lines."""
    lines = (REPO_ROOT / "packages/extra" / name / "build.sh").read_text(encoding="utf-8").split("\n")
    opener = [i for i, text in enumerate(lines) if re.search(r"<<\s*'HELPEREOF'\s*$", text)]
    if len(opener) != 1:
        raise AssertionError(f"{name}: the installer's heredoc opens {len(opener)} times, not once")
    return lines[opener[0] + 1:lines.index("HELPEREOF", opener[0] + 1)]


def _segment(name):
    """The installer's plain assignments before its record begins, and its lines from the lookup up to the install
    step."""
    body = _installer(name)
    begins = [i for i, text in enumerate(body) if text.startswith(f'igos_helper_init "{name}"')]
    lookup = [i for i, text in enumerate(body)
              if "LATEST=$(igos_helper_find_verified_deb_in_packages " in text]
    if len(begins) != 1 or len(lookup) != 1:
        raise AssertionError(f"{name}: the record begins {len(begins)} times and the lookup is made "
                             f"{len(lookup)} times, not once each")
    install = [i for i in range(lookup[0], len(body)) if INSTALL_STEP.match(body[i])]
    if not install:
        raise AssertionError(f"{name}: no install step after the lookup")
    assignments = [text for text in body[:begins[0]]
                   if ASSIGNMENT.match(text) and "$(" not in text
                   and not text.startswith("IGOS_HELPER_USER_CLEANUP=")]
    return assignments, body[lookup[0]:install[0]]


def _fire(name, scratch, find, wget=WGET_OK, ar=AR_OK, tar=TAR_OK):
    """The segment under set -e, its steps stood in for, in SCRATCH."""
    assignments, segment = _segment(name)
    work = scratch / "work"
    work.mkdir()
    script = "\n".join([
        "set -e",
        *assignments,
        f"TMPDIR={work}",
        f"IGOS_HELPER_MANIFEST_DIR={scratch}/helpers",
        "igos_helper_set_version() { :; }",
        "igos_helper_verify_deb_via_signed_release() { return 0; }",
        f"igos_helper_find_verified_deb_in_packages() {{ {find}; }}",
        f"wget() {{ {wget}; }}",
        'igos_helper_download() { local to=$1 url=$2 via=$3; shift 3; "$via" "$@" -O "$to" "$url"; }',
        f"ar() {{ {ar}; }}",
        f"tar() {{ {tar}; }}",
        *segment,
        'echo "SEGMENT PASSED"',
    ]) + "\n"
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(scratch)}
    return subprocess.run(["/bin/bash", "-c", script], cwd=work, env=env, capture_output=True, text=True,
                          timeout=60)


class InstallerRefusalsAreReached(unittest.TestCase):
    def _each(self, find, wget=WGET_OK, ar=AR_OK, tar=TAR_OK, names_what_it_leaves=True):
        """Fire every helper's segment; each must stop with exit 1 at a refusal it prints."""
        problems = []
        for name in HELPERS:
            with tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp)
                proc = _fire(name, scratch, find, wget, ar, tar)
                said = re.sub(r"\s+", " ", proc.stdout)
                if proc.returncode != 1 or "SEGMENT PASSED" in said or "ERROR:" not in said:
                    problems.append(f"{name}: exit {proc.returncode}, no refusal printed; "
                                    f"stdout {said[-160:]!r}; stderr {proc.stderr.strip()[-160:]!r}")
                    continue
                if names_what_it_leaves:
                    leaves = (f"a note of this stopped attempt for pkm ({scratch}/helpers/{name}.manifest.partial) "
                              f"and the run's working files under /tmp.")
                    if "Nothing was installed, and nothing on this machine was changed beyond the record of " \
                            "your acceptance of" not in said or leaves not in said:
                        problems.append(f"{name}: the refusal does not name what the run leaves: {said[-400:]!r}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_step_succeeding_passes_the_segment(self):
        # The control: with every step standing in as a success, the segment
        # reaches the install step, so a refusal below is the step's own.
        problems = []
        for name in HELPERS:
            with tempfile.TemporaryDirectory() as tmp:
                proc = _fire(name, Path(tmp), FIND_OK)
                if proc.returncode != 0 or "SEGMENT PASSED" not in proc.stdout:
                    problems.append(f"{name}: exit {proc.returncode}; {proc.stdout[-200:]!r} {proc.stderr[-200:]!r}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_a_lookup_that_fails_reaches_its_refusal(self):
        # The lookup returns non-zero for a version the listing does not
        # carry and for any metadata it cannot fetch or verify.
        self._each(FIND_FAILS, names_what_it_leaves=False)

    def test_a_lookup_that_finds_nothing_reaches_its_refusal(self):
        self._each(FIND_EMPTY, names_what_it_leaves=False)

    def test_a_download_that_fails_reaches_its_refusal(self):
        self._each(FIND_OK, wget=WGET_FAILS)

    def test_a_package_ar_cannot_open_reaches_its_refusal(self):
        self._each(FIND_OK, ar=AR_FAILS, tar=TAR_FAILS)

    def test_a_data_archive_tar_cannot_unpack_reaches_its_refusal(self):
        self._each(FIND_OK, tar=TAR_FAILS)

    def test_a_package_without_a_data_archive_reaches_its_refusal(self):
        self._each(FIND_OK, ar=AR_OK_NO_DATA, tar=TAR_FAILS)


if __name__ == "__main__":
    unittest.main()
