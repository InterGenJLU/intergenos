"""The Unicode Character Database ibus ships must be staged, not just unzipped.

ibus needs the UCD readable at the real path /usr/share/unicode/ucd/ while its
own configure runs, so build.sh unzips it onto the live tree at configure time.
For a long while that was the ONLY copy: `make install` does not carry the UCD,
so roughly 40 MB of real payload reached every image with no package manifest
recording it. The squashfs ownership gate could only be held fail-closed
against new classes by carrying a standing `usr/share/unicode/ucd/**`
exception, and that exception's own reason named the fix it was waiting for —
stage it into DESTDIR the way aspell's dictionary is staged.

This is that fix's regression guard. The property is not "the recipe mentions
the UCD" but the two things that make the payload owned:

  1. do_install extracts the pinned zip into DESTDIR, so the archive carries
     the bytes and the builder's manifest records them.
  2. The recipe asserts the extraction actually produced NamesList.txt, so an
     upstream zip whose layout changes fails loudly instead of shipping an
     empty directory while the manifest claims a Unicode database.

The allowlist row is checked too, because leaving it behind would keep the
exception alive and hide the next thing that lands under that path.

Nothing here builds, needs privilege, or reads the network — it reads the
recipe and the allowlist as text.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
IBUS = REPO / "packages" / "desktop" / "ibus"
BUILD_SH = IBUS / "build.sh"
PACKAGE_YML = IBUS / "package.yml"
ALLOWLIST = REPO / "config" / "squashfs-ownership-allowlist.txt"

UCD_PATH = "usr/share/unicode/ucd"


def _do_install_body() -> str:
    """The text of build.sh's do_install function, and only that function.

    Read as its own unit deliberately: the configure-time unzip targets the
    LIVE tree on purpose and must not be mistaken for the staging step. A test
    that searched the whole file would pass on the very bug this guards.
    """
    text = BUILD_SH.read_text(encoding="utf-8")
    match = re.search(r"^do_install\(\)\s*\{(.*?)^\}", text, re.S | re.M)
    assert match, "packages/desktop/ibus/build.sh has no do_install() function"
    return match.group(1)


def test_do_install_extracts_the_ucd_into_destdir() -> None:
    body = _do_install_body()
    staged = [
        line for line in body.splitlines()
        if "DESTDIR" in line and UCD_PATH in line and "unzip" in line
    ]
    assert staged, (
        "ibus do_install does not extract the UCD into DESTDIR.\n"
        "The configure-time unzip writes to the live tree so configure can read "
        "it; that copy is invisible to the manifest. Without a DESTDIR "
        "extraction the payload ships unowned and the squashfs ownership gate "
        "needs its exception back.")


def test_do_install_asserts_the_ucd_landed() -> None:
    """An extraction that silently produced nothing is the failure to catch."""
    body = _do_install_body()
    assert "NamesList.txt" in body, (
        "ibus do_install stages the UCD but never asserts a known member "
        "landed — an upstream zip whose layout changes would leave an empty "
        "directory while the package claims a Unicode database (Rule 21: a "
        "stub is a lie).")
    asserted = re.search(
        r"if\s*\[\s*!\s*-f\s*\"?\$\{?DESTDIR\}?[^\"]*NamesList\.txt", body)
    assert asserted, (
        "the NamesList.txt mention in do_install is not a check on the DESTDIR "
        "copy — the assertion must test what was staged, not what configure "
        "left on the live tree.")


def test_the_ucd_zip_is_a_pinned_source() -> None:
    """The loud-failure branch is only honest if the zip is guaranteed staged.

    do_install fails closed when UCD.zip is absent. That is the right behaviour
    only because the zip is a sha256-pinned source entry, so verify-sources has
    already refused a missing or altered file before any build phase runs.
    """
    yml = PACKAGE_YML.read_text(encoding="utf-8")
    assert "UCD.zip" in yml, (
        "packages/desktop/ibus/package.yml no longer declares UCD.zip as a "
        "source — do_install's fail-closed branch would then be refusing a "
        "condition the build never guaranteed")
    ucd_line = next(
        (i for i, line in enumerate(yml.splitlines()) if "UCD.zip" in line),
        None)
    following = yml.splitlines()[ucd_line: ucd_line + 2]
    assert any("sha256:" in line for line in following), (
        "the UCD.zip source entry carries no sha256 pin, so nothing verifies "
        "the bytes this package now claims as its own payload")


def test_verify_paths_declares_the_staged_ucd() -> None:
    """Rule 20: the payload a package ships is proven on the chroot."""
    yml = PACKAGE_YML.read_text(encoding="utf-8")
    assert f"/{UCD_PATH}/NamesList.txt" in yml, (
        "ibus stages the UCD but does not declare it in verify_paths, so the "
        "pre-squashfs audit would not prove the payload reached the chroot")


def test_the_allowlist_exception_is_gone() -> None:
    """A live exception would keep accepting anything under that path."""
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern = re.split(r"\t+|\s{2,}", line, maxsplit=1)[0]
        assert not pattern.startswith(UCD_PATH), (
            f"config/squashfs-ownership-allowlist.txt still carries {pattern!r}. "
            "The recipe now stages the UCD into DESTDIR, so the manifest "
            "records it and the exception is no longer owed — and while it "
            "stands, anything else appearing under that path ships unowned "
            "without the gate saying a word.")
