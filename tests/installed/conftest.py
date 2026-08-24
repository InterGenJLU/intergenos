"""Gate tier that runs against an INSTALLED InterGenOS system, not the source tree.

WHY THIS TIER EXISTS. Every P0/P1 in the R001.1 recovery scope is a COMPOSITION
property: a permission that depends on the real umask under a real HOME, a privilege
boundary that depends on the real hardened unit, a routing decision that depends on
the real embedding corpus and the real embedding server. None of them can fail in a
source-tree unit test, and none of them did — the release shipped with unit tests
passing. This tier is the layer that was missing.

TWO THINGS THIS FILE HAS TO DEFEND AGAINST, both measured rather than assumed.

1. THE PROJECT ROOT CONFTEST DELIBERATELY ISOLATES THE THINGS THIS TIER MUST READ.
   ``conftest.py`` at the repository root points XDG_STATE_HOME, XDG_DATA_HOME,
   XDG_CACHE_HOME and XDG_CONFIG_HOME at a throwaway directory before any test
   imports, so an ordinary test run can never touch production state. That is
   correct and must not be weakened. It also means a gate here that resolved
   ``~/.local/share/intergen`` through the XDG variables would inspect an empty
   temporary directory and PASS while the real installed system was defective —
   the exact silent-green this tier exists to prevent. Every path in this tier is
   therefore resolved from the real account's home directory, read from the
   password database, never from the environment.

2. A TIER THAT CANNOT MEASURE MUST NOT LOOK GREEN. These gates are collected only
   when INTERGENOS_INSTALLED_GATES=1 is set, because they are invoked from the
   post-install evaluation checklist and the promotion checklist rather than from
   an ordinary developer's ``pytest``. When the variable is absent the tier SKIPS
   with a reason that says, in words, that nothing was verified. When the variable
   is set but the machine is not an installed InterGenOS system, the tier FAILS —
   it does not skip. "I could not check" is never allowed to read as "I checked".
"""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

import pytest

GATE_ENV = "INTERGENOS_INSTALLED_GATES"

# An installed package lives under the system library tree. Anything else is
# source, and this tier has no business reporting on source.
_INSTALLED_PREFIXES = ("/usr/lib/", "/usr/lib64/", "/usr/local/lib/")


def _looks_installed(path: str) -> bool:
    return path.startswith(_INSTALLED_PREFIXES) and "/site-packages/" in path


def _repair_import_path() -> list[str]:
    """Take the source checkout back off sys.path before anything imports.

    MEASURED 2026-08-24, and the reason this function exists: the tier was
    importing the SOURCE TREE, not the installed package, in every invocation —
    from inside the checkout and from outside it alike. The repository-root
    conftest inserts the project root at sys.path[0] so that ordinary tests can
    import the project, which is right for every other tier and fatal for this
    one: `import intergen` then resolves to the checkout ahead of
    site-packages, and gates written to measure the shipped system measured the
    source instead. That is the exact silent-green this tier exists to end,
    sitting inside the instrument.

    Repairing the path is not enough on its own and is not trusted on its own —
    :func:`require_installed_intergenos` below ASSERTS the outcome afterwards.
    This function removes the cause; that assertion is what refuses to report if
    the cause is still there.

    Returns the entries removed, so the failure message can name them.
    """
    removed: list[str] = []
    for entry in list(sys.path):
        if not entry:
            continue
        try:
            shadows = (Path(entry) / "intergen" / "__init__.py").is_file()
        except OSError:
            shadows = False
        if shadows and not _looks_installed(str(Path(entry) / "intergen")):
            sys.path.remove(entry)
            removed.append(entry)
    if removed:
        # Anything already imported from the checkout has to go too, or the
        # repaired path changes nothing for a module that is already resident.
        for name in [m for m in sys.modules
                     if m == "intergen" or m.startswith("intergen.")]:
            del sys.modules[name]
    return removed


_REMOVED_FROM_PATH: list[str] = (
    _repair_import_path() if os.environ.get(GATE_ENV) == "1" else [])

_SKIP_REASON = (
    f"NOT VERIFIED: the installed-system gate tier did not run because {GATE_ENV}=1 "
    "was not set. This is a SKIP, not a pass: none of the composition properties "
    "this tier covers have been checked. It is invoked from the post-install "
    "evaluation checklist and the promotion checklist."
)


def pytest_collection_modifyitems(config, items):
    """Deselect the whole tier unless it was asked for, with a loud reason.

    When the tier IS asked for, it must be the only thing in the session. This
    tier takes the source checkout off sys.path so it can import the installed
    package (see :func:`_repair_import_path`); every other tier in this
    repository imports the project FROM that checkout and would break. Refusing
    a mixed session is how that repair stays safe to make.
    """
    if os.environ.get(GATE_ENV) != "1":
        skip = pytest.mark.skip(reason=_SKIP_REASON)
        here = Path(__file__).resolve().parent
        for item in items:
            try:
                in_tier = here in Path(str(item.fspath)).resolve().parents
            except OSError:
                in_tier = False
            if in_tier:
                item.add_marker(skip)
        return

    here = Path(__file__).resolve().parent
    outsiders = []
    for item in items:
        try:
            in_tier = here in Path(str(item.fspath)).resolve().parents
        except OSError:
            in_tier = False
        if not in_tier:
            outsiders.append(str(item.nodeid))
    if outsiders:
        raise pytest.UsageError(
            f"{GATE_ENV}=1 selects the installed-system gate tier, which must "
            f"run ALONE: it removes the source checkout from sys.path so it can "
            f"import the INSTALLED package, and the rest of this repository's "
            f"tests import the project from that checkout. "
            f"{len(outsiders)} test(s) outside the tier were collected, "
            f"starting with {outsiders[0]}. Run the tier by itself "
            f"(scripts/run-installed-gates.py does).")
    return


def _real_home() -> Path:
    """The invoking account's REAL home directory.

    Read from the password database rather than $HOME, because the project-root
    conftest rewrites the XDG variables and a future change could rewrite HOME the
    same way. The password database is the one source that a test-harness
    convenience cannot move.
    """
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@pytest.fixture(scope="session")
def real_home() -> Path:
    return _real_home()


@pytest.fixture(scope="session")
def os_release() -> dict[str, str]:
    """/etc/os-release parsed. Absent or unparseable is a FAILURE, never a skip."""
    path = Path("/etc/os-release")
    if not path.is_file():
        pytest.fail(
            "/etc/os-release is absent. This tier only has meaning on an installed "
            "InterGenOS system; refusing to report anything about a machine it "
            "cannot identify."
        )
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


@pytest.fixture(scope="session", autouse=True)
def require_installed_intergenos(os_release):
    """Refuse to run against anything that is not an installed InterGenOS system.

    THREE CHECKS, and the third is the one that was missing. Presence of the
    installed directory says the shipped package EXISTS; it says nothing about
    which package the gates are importing. Until 2026-08-24 this fixture stopped
    at presence, and the tier imported the source checkout in every invocation
    while reporting as though it had measured the installed system. Asking where
    ``intergen`` actually resolved is the only check that could have caught that,
    so it is now asked, and it is asked of the imported module rather than of the
    filesystem.
    """
    if os_release.get("ID") != "intergenos":
        pytest.fail(
            "This machine does not identify as InterGenOS "
            f"(ID={os_release.get('ID')!r}). The installed-system gates measure "
            "properties of a real InterGenOS install; running them elsewhere would "
            "produce a verdict about nothing."
        )
    site = Path("/usr/lib") / f"python3.{sys.version_info.minor}" / "site-packages" / "intergen"
    if not site.is_dir():
        pytest.fail(
            f"The installed assistant package is not present at {site}. This tier "
            "reads the SHIPPED modules, not the source tree, so it cannot run here."
        )

    import intergen
    resolved = intergen.__file__ or ""
    if not _looks_installed(resolved):
        pytest.fail(
            "THIS TIER IS MEASURING THE WRONG SOFTWARE.\n"
            f"  `import intergen` resolved to: {resolved}\n"
            f"  the installed package is at:   {site}\n"
            "Every gate below reads the imported package, so a verdict from here "
            "would describe the source tree and would then be quoted as evidence "
            "about a release nobody installed.\n"
            f"  entries removed from sys.path before import: "
            f"{_REMOVED_FROM_PATH or 'none'}\n"
            f"  sys.path[0:4] now: {sys.path[0:4]}\n"
            "Run this tier by itself, from outside any checkout "
            "(scripts/run-installed-gates.py does exactly that)."
        )


@pytest.fixture(scope="session")
def installed_intergen_dir() -> Path:
    """The SHIPPED assistant package directory — the thing that actually runs."""
    import sys
    return Path("/usr/lib") / f"python3.{sys.version_info.minor}" / "site-packages" / "intergen"


@pytest.fixture(scope="session", autouse=True)
def refuse_to_measure_a_source_tree(installed_intergen_dir):
    """The `intergen` these gates import MUST be the installed one.

    Some gates in this tier read shipped files by path; others read the compiled
    code objects of `intergen.web_server` and `intergen.router`, which resolve
    through sys.path. Run from a checkout, those imports bind to the CHECKOUT,
    so the gate reports on the tree the author is editing while every message it
    prints says "the shipped modules". A checkout that already carries the fix
    then reads as an installed system that carries the fix — the exact
    silent-green this tier exists to prevent, and it is silent because a pass
    looks the same either way.

    Refusing here is a FAILURE, not a skip: "I measured the wrong thing" must
    never read as "I checked".
    """
    import intergen
    imported = Path(intergen.__file__).resolve().parent
    if imported != installed_intergen_dir.resolve():
        pytest.fail(
            "This tier imported the assistant package from\n"
            f"  {imported}\n"
            "but the installed package is at\n"
            f"  {installed_intergen_dir}\n"
            "so the gates that read compiled code objects would report on that "
            "directory instead of on what this machine actually runs. Run the "
            "tier from a directory where `import intergen` resolves to the "
            "installed package (a checkout on sys.path shadows it)."
        )
