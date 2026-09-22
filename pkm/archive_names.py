# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The one place that knows what a binary package archive is called.

Decided 2026-09-22. A binary package archive is named
``<name>-<version>-<release>.igos.tar.gz``. Before this module the name was
composed by six separate f-strings and taken apart by five separate regular
expressions, and the two halves had drifted apart: the producers wrote
``<name>-<version>.igos.tar.gz`` while pkm's download cache had already moved
to the release-carrying form so that the release a machine is upgrading away
from survives on disk long enough to be rolled back to. An archive whose name
cannot state its release cannot be told apart from the build it replaces.

Two rules govern everything here, and both are refusals rather than guesses.

**A release that was never stated is never invented.** The bash build tier
archives recipe-less LFS core packages that carry no ``release:`` field, and
the published R001.2 archives were named before this change. Both keep the
release-less name. Writing ``-1`` would assert a build number nothing
recorded.

**A release is never read out of a filename alone.** Seventeen packages carry
an upstream version whose own tail reads as a release suffix
(``dialog-1.3-20260107``, ``re2-2025-11-05``, ``imagemagick-7.1.2-13``), so
``<stem>-<digits>`` is ambiguous by construction. :func:`parse_archive_filename`
resolves a release only when the caller supplies the versions the recipes
state; without them it reports ``release=None`` and leaves the whole tail as
the version, which is exactly the reading every caller had before.
"""

from __future__ import annotations

import re
from typing import Mapping, NamedTuple, Optional, Union

#: The suffix every binary package archive carries.
SUFFIX = ".igos.tar.gz"

#: What a release looks like in text: ASCII digits only, with no leading zero
#: unless the release IS zero. ``str.isdigit`` is not this test -- it accepts
#: every Unicode digit, so an Arabic-Indic three read as release 3 and a
#: superscript two passed the test and then raised out of ``int()``; and a
#: padded ``01`` read as release 1, putting two names on one build. Decided
#: 2026-09-22 after a second reader measured all three.
_RELEASE_TEXT = re.compile(r"(?:0|[1-9][0-9]*)")

#: The last resort when no recipe version is known: the shortest leading run
#: of fields that is followed by something starting with a digit. This is the
#: reading ``scripts/inject-pkginfo.py`` already applied to recipe-less
#: archives, kept verbatim so that path's behaviour does not change.
_NAME_THEN_VERSION = re.compile(r"^(.+?)-(\d.*)$")


class ArchiveName(NamedTuple):
    """What a filename says about the build it holds.

    ``release`` is ``None`` when the name does not state one, which is not the
    same as release 1 — it means this filename cannot identify the build.
    """

    name: str
    version: str
    release: Optional[int]


def _normalise_release(release: Union[int, str, None]) -> Optional[int]:
    if release is None:
        return None
    if isinstance(release, bool):          # bool is an int; it is not a release
        raise ValueError(f"release is not a whole number: {release!r}")
    if isinstance(release, int):
        if release < 0:
            raise ValueError(f"release is not a whole number: {release!r}")
        return release
    text = str(release)
    if text == "":
        return None
    if not _RELEASE_TEXT.fullmatch(text):
        raise ValueError(
            f"release is not a whole number written in ASCII digits without "
            f"leading zeros: {release!r}")
    return int(text)


def archive_filename(name: str, version: str,
                     release: Union[int, str, None] = None) -> str:
    """The filename for one built package.

    ``release`` may be an int, a string of digits, ``None`` or ``""``. The last
    two mean "this build states no release", and the release-less name is
    returned. Anything else that is not a whole number is refused rather than
    coerced: a wrong release asserted as fact is worse than none.
    """
    if not name:
        raise ValueError("a package archive needs a name")
    if not version:
        raise ValueError(f"a package archive needs a version: {name!r}")
    rel = _normalise_release(release)
    if rel is None:
        return f"{name}-{version}{SUFFIX}"
    return f"{name}-{version}-{rel}{SUFFIX}"


def parse_archive_filename(
        filename: str,
        known: Optional[Mapping[str, str]] = None) -> Optional[ArchiveName]:
    """Read a filename back, without guessing at a release.

    ``known`` maps a package name to the version its recipe states. When a
    known name and version account for the stem exactly, the remainder (if
    any) is the release. Without ``known``, or when nothing matches, the
    release is reported as ``None`` and the whole tail stays in the version —
    a name this function cannot justify is never turned into a claim.

    Returns ``None`` when the argument is not a binary package archive name at
    all, so a caller can tell "not mine" from "mine, release unknown".
    """
    base = filename.rsplit("/", 1)[-1]
    if not base.endswith(SUFFIX):
        return None
    stem = base[:-len(SUFFIX)]
    if not stem:
        return None

    if known:
        # Longest recipe name first: the ch8 twins (gcc and gcc-core) both
        # prefix the same stem, and the shorter one would swallow "-core" into
        # the version.
        for name in sorted(known, key=len, reverse=True):
            version = known[name]
            if not version:
                continue
            exact = f"{name}-{version}"
            if stem == exact:
                return ArchiveName(name, version, None)
            if stem.startswith(exact + "-"):
                tail = stem[len(exact) + 1:]
                # Only a tail written the way a release is written is one. A
                # tail in other digits, or padded, is not turned into a claim:
                # it falls through to the release-less reading below.
                if _RELEASE_TEXT.fullmatch(tail):
                    return ArchiveName(name, version, int(tail))

    match = _NAME_THEN_VERSION.match(stem)
    if not match:
        return None
    return ArchiveName(match.group(1), match.group(2), None)


def candidate_filenames(name: str, version: str,
                        release: Union[int, str, None] = None) -> list:
    """Every name one built package may be on disk under, best first.

    A staging directory during and after this change holds both shapes: a
    package whose recipe states a release is archived under the
    release-carrying name, and the recipe-less packages the bash tier builds
    are archived under the release-less one, as is every archive published
    before this change. A reader looking for "the archive of this package"
    must therefore try both, in that order, rather than assume either.

    The order is what makes this safe to use: the exact, release-carrying
    name is always preferred, so a reader never settles for a file that
    cannot name its build while the one that can is sitting beside it.
    """
    exact = archive_filename(name, version, release)
    plain = archive_filename(name, version, None)
    return [exact] if exact == plain else [exact, plain]
