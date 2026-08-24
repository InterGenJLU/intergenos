"""Project-root pytest configuration: ensure project root is importable.

Several test files load InterGenOS scripts via importlib.util.spec_from_file_location
(because scripts/ has no __init__.py and many script filenames contain hyphens).
Those scripts often import project-internal packages like ``pkm.repo``, which
require the project root to be on ``sys.path``.

When test directories have inconsistent ``__init__.py`` presence (some packages,
some loose test files), pytest's automatic ``sys.path`` insertion can fail to
include the project root reliably during collection. Placing this conftest at
the project root ensures it runs before any test-file import and the project
root is always on sys.path.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 2B-LANE hygiene rider (2026-07-09): isolate test XDG state from production.
# intergen's dispatch ledger (tool-dispatch.jsonl) and the trace/metrics logs
# resolve under XDG_STATE_HOME (falling back to ~/.local/state), so a bare pytest
# run appended to the PRODUCTION ~/.local/state/intergen/ ledger and reconfigured
# production logging. Point every XDG base at a throwaway dir BEFORE any intergen
# import so a test run never touches production state; cleaned at process exit.
_XDG_TMP = tempfile.mkdtemp(prefix="igos-pytest-xdg-")
atexit.register(lambda: shutil.rmtree(_XDG_TMP, ignore_errors=True))
for _xdg_var, _xdg_sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"),
                           ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
    _xdg_dir = os.path.join(_XDG_TMP, _xdg_sub)
    os.makedirs(_xdg_dir, exist_ok=True)
    os.environ[_xdg_var] = _xdg_dir

# HOME joins the same isolation, for the same reason and one more.
#
# Redirecting only the XDG_* variables leaves a gap: several modules resolve
# their per-user paths through Path.home() DIRECTLY rather than through an
# XDG base — session_manager.SESSIONS_DIR, console.shell.HISTORY_FILE and
# mcp_client's pin directory all do — so a bare pytest run still reached the
# real home through those.
#
# The gap became a state-changing one when the daemon entry point started
# performing a one-time permission pass over the user's per-user trees at
# startup: three cases in intergen/tests/test_eval_consent.py drive
# dbus_daemon.main() to pin its argv contract, and main() runs that pass
# before the daemon is constructed — so a test run adjusted the modes of the
# invoking user's own conversation transcripts and fact database. Measured on
# 2026-08-24: three directories and six files under the real home changed
# during a suite run. The pass only ever removes group and other access, so
# nothing was lost or widened, but a test run must not touch the real home at
# all.
#
# Set here rather than per-test, for the same reason as the block above: it
# covers any FUTURE test that reaches a per-user path, which is the seam the
# defect actually lived in.
#
# ONE consumer legitimately needs the REAL home and is preserved explicitly:
# scripts/check-public-content.py loads its private pattern groups from
# ~/.config/intergenos/public-content-patterns and REFUSES fail-closed when it
# cannot read them, which is correct behaviour — a scan missing a whole tier
# that still reported PASS would be indistinguishable from a clean tree. Its
# own documented override is pointed at the real file BEFORE HOME moves, so the
# gate's tests keep exercising the real pattern set. Nothing else in the suite
# was found to read a real home-relative file: the redirect was measured across
# the full suite, and this was the only consumer it disturbed.
_REAL_PATTERNS = os.path.join(
    os.path.expanduser("~"), ".config", "intergenos", "public-content-patterns")
if "IGOS_PUBLIC_CONTENT_PATTERNS" not in os.environ and os.path.exists(_REAL_PATTERNS):
    os.environ["IGOS_PUBLIC_CONTENT_PATTERNS"] = _REAL_PATTERNS

_HOME_TMP = os.path.join(_XDG_TMP, "home")
os.makedirs(_HOME_TMP, exist_ok=True)
os.environ["HOME"] = _HOME_TMP

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Pre-import pkm so that scripts loaded via importlib.util.spec_from_file_location
# (e.g., tests/repo-publish/test_generate_repodb.py loading scripts/generate-repodb.py)
# find pkm.repo in sys.modules even when pytest's collection ordering interferes
# with their module-level `from pkm.repo import ...`. Pre-loading here is cheap
# (~25ms one-time cost) and removes a class of "depends-on-collection-order" failures.
try:
    import pkm.repo  # noqa: F401
except ImportError:
    # If pkm itself can't import (e.g., missing dependency), let the test that
    # actually needs it surface the real error rather than swallowing it here.
    pass

# PI-234: point pkm's pre-transaction handler directory at an EMPTY throwaway
# dir for the whole run.
#
# pkm's mutating commands (cmd_install / cmd_upgrade / cmd_remove) call
# pretxn.run_pre_transaction_hook, which defaults to the live drop-in directory
# /usr/lib/pkm/pre-transaction.d. On an installed InterGenOS machine that
# directory holds the backup engine's restore-point handler, so any test that
# drives one of those commands executed the RUNNING system's handler against
# the RUNNING engine: a real privileged, state-changing action taken by a test
# run, and the path that raises interactive authentication on a desktop
# session. Measured on an installed system: seven tests across
# tests/pkm/test_upgrade_ordering.py, tests/pkm/test_upgrade_rehash_threading.py
# and tests/pkm/test_available_updates_refresh.py spawned the live handler, and
# one of them blocked on the engine socket for minutes.
#
# The isolation is set here rather than per-test so it covers any FUTURE test
# that drives a mutating command — the defect was the seam, not those seven
# tests. It redirects only the directory the hook reads; the hook logic itself
# is still exercised, by tests that pass their own handler_dir.
_PRETXN_TMP = tempfile.mkdtemp(prefix="igos-pytest-pretxn-")
atexit.register(lambda: shutil.rmtree(_PRETXN_TMP, ignore_errors=True))
try:
    import pkm.pretxn as _pkm_pretxn

    _pkm_pretxn.PRETXN_HANDLER_DIR = Path(_PRETXN_TMP)
except ImportError:
    # Same reasoning as the pkm.repo pre-import above: surface the real error
    # at the test that needs the module, not here.
    pass

# Point model_choice.detect_driver_state's DEFAULT sysfs root at an EMPTY
# throwaway dir for the whole run, so no test reads the GPU hardware of the
# machine it happens to be running on.
#
# THE DEFECT THIS CLOSES. `setup.run_setup` and `dbus_daemon` both call
# `model_choice.build_offer(...)` without a `driver_state`, and build_offer then
# falls back to `detect_driver_state()`, which walks the real /sys/class/drm.
# On a machine whose NVIDIA card is bound to the open-source driver that raises
# the driver advisory, and `_choose_tier` then asks one more question than a
# scripted test supplies — four tests in test_setup_model_pick.py died with
# StopIteration on exactly one development machine and passed on every other. That
# specific file now pins the probe itself; this block is what stops the NEXT
# test from inheriting the same trap, which is the seam the defect actually
# lived in.
#
# WHY THE DEFAULT AND NOT A MOCK. Only the default argument moves. The real
# parsing code still runs, against an empty directory, and returns the
# no-NVIDIA state — so tests that exercise the parser by passing their OWN
# drm_root (intergen/tests/test_model_choice.py does this six times) are
# completely unaffected and keep testing the real thing. Mocking the function
# would have broken them and would have tested less.
#
# The mismatch check is deliberately FATAL. If the signature ever changes, a
# silent no-op here would put the whole suite back to reading host hardware
# while still looking isolated, and that is the failure mode this exists to
# prevent — a loud collection error is the safe direction to fail.
_DRM_TMP = tempfile.mkdtemp(prefix="igos-pytest-drm-empty-")
atexit.register(lambda: shutil.rmtree(_DRM_TMP, ignore_errors=True))
try:
    import inspect as _inspect

    from intergen import model_choice as _model_choice

    _drm_params = list(_inspect.signature(
        _model_choice.detect_driver_state).parameters)
    if _drm_params != ["drm_root"]:
        raise RuntimeError(
            "conftest: intergen.model_choice.detect_driver_state's signature "
            f"changed (parameters are {_drm_params!r}, expected ['drm_root']). "
            "The test suite pins this function's DEFAULT sysfs root so tests "
            "never read the host's real GPU; that pin no longer applies "
            "cleanly. Update this block deliberately rather than letting the "
            "suite silently start reading host hardware again."
        )
    _model_choice.detect_driver_state.__defaults__ = (_DRM_TMP,)
except ImportError:
    # Same reasoning as the two blocks above: if intergen itself cannot import,
    # let the test that needs it surface the real error.
    pass
