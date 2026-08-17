"""RED/GREEN fixture suite for scripts/build-watcher.sh (work-plan 1.15, spec §5.1).

Exercises the watcher's pure detection logic through its offline surfaces
(--replay / --classify-halt) against distilled slices of the two REAL banked
glibc logs plus synthetic budget/halt fixtures. The one-time full-log replay
(the 941 MB launch-4 + 33 MB launch-5 logs on jarvis-storage) is the separate
acceptance leg run at burn time; these committed fixtures guard the signatures.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = _PROJECT_ROOT / "scripts" / "build-watcher.sh"
FIX = Path(__file__).resolve().parent / "fixtures"


def watch(*args):
    """Run build-watcher.sh with args; return (stdout-stripped, returncode)."""
    r = subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True,
    )
    return r.stdout.strip(), r.returncode


class TestScriptHygiene:
    def test_syntax_ok(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_no_bare_ampersand_backgrounding(self):
        # The orphaned-ssh class: the script must never background anything itself.
        text = SCRIPT.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            # a bare "&" job-control operator (not "&&", not "2>&1", not "$&")
            stripped = code.rstrip()
            if stripped.endswith("&") and not stripped.endswith("&&"):
                pytest.fail(f"bare-& backgrounding at line {i}: {line!r}")


class TestRecursionSignature:
    def test_launch4_slice_alarms_on_configure_runs(self):
        # REAL distilled slice of the launch-4 known-bad glibc log: 5 configure
        # runs (> 2) MUST fire RECURSION-SIGNATURE.
        out, rc = watch("--pkg", "glibc", "--replay",
                        str(FIX / "recursion_glibc_launch4.log"))
        assert rc == 0
        assert out.startswith("RECURSION-SIGNATURE glibc configure_runs=")
        n = int(out.rsplit("=", 1)[1])
        assert n > 2

    def test_make_syscalls_over_120_alarms(self):
        out, rc = watch("--pkg", "glibc", "--replay",
                        str(FIX / "make_syscalls_recursion.log"))
        assert out == "RECURSION-SIGNATURE glibc make_syscalls=121"
        assert rc == 0

    def test_launch5_healthy_slice_stays_quiet(self):
        # REAL distilled slice of the launch-5 known-healthy dual-width pass:
        # 2 configure runs + a legitimate make-syscalls burst MUST stay QUIET.
        out, rc = watch("--pkg", "glibc", "--replay",
                        str(FIX / "healthy_glibc_dualwidth.log"))
        assert out.startswith("REPLAY-CLEAN")
        assert "RECURSION" not in out
        assert rc == 0

    def test_recursion_is_glibc_scoped(self):
        # The same healthy fixture under a NON-glibc package identity is not
        # subject to the configure/make-syscalls discriminators (v1 is scoped).
        out, _ = watch("--pkg", "somelib", "--replay",
                       str(FIX / "recursion_glibc_launch4.log"))
        assert out.startswith("REPLAY-CLEAN")


class TestBudget:
    def test_3x_default_alarms(self):
        out, rc = watch("--replay", str(FIX / "budget_alarm_3x.log"))
        assert out.startswith("BUDGET-ALARM ")
        assert "ratio=3.5" in out
        assert rc == 0

    def test_5x_default_halts(self):
        out, rc = watch("--replay", str(FIX / "budget_halt_5x.log"))
        assert out.startswith("BUDGET-HALT ")
        assert "ratio=5.5" in out
        assert rc == 0


class TestUnitDeadClassify:
    def test_clean_stop_after(self):
        out, rc = watch("--classify-halt", str(FIX / "halt_clean_stopafter.log"))
        assert out == "clean --stop-after extra"
        assert rc == 0

    def test_failure(self):
        out, rc = watch("--classify-halt", str(FIX / "halt_failure.log"))
        assert out.startswith("failure: ")
        assert "FAILED in build" in out
        assert rc == 0


class TestHaltLine:
    def test_failure_log_replays_as_halt_line(self):
        out, rc = watch("--replay", str(FIX / "halt_failure.log"))
        assert out.startswith("HALT-LINE ")
        assert "FAILED in build" in out
        assert rc == 0


class TestBudgetTable:
    def test_measured_rows_present_with_correct_keys(self):
        tsv = (_PROJECT_ROOT / "scripts" / "data" / "sbu-budgets.tsv").read_text()
        rows = [l.split("\t")[0] for l in tsv.splitlines()
                if l and not l.startswith("#")]
        # keys must match the real LOG-derived names after the phase-marker
        # strip (spec §4; live-defect #3 2026-07-09: the ch8 driver logs
        # "gcc-ch8-<ts>.log" -> key "gcc"; "gcc-core" is the recipe dir, not a
        # log name, and never matches)
        for key in ("binutils-pass1", "glibc", "gcc", "webkitgtk",
                    "libreoffice", "firefox", "thunderbird"):
            assert key in rows, f"missing budget row: {key}"
        # the webkit->webkitgtk key correction holds; the old wrong gcc key stays dead
        assert "webkit" not in rows
        assert "gcc-core" not in rows


class TestPkgFromLogname:
    """Live-defect #3 (2026-07-09): phase-marker stripping + the two-root scan.

    The chroot drivers log "<pkg>-chroot-<ts>.log" / "<pkg>-ch8-<ts>.log" /
    "<pkg>-ch10-<ts>.log" into the chroot copy's log dir; the derived key must
    strip the marker so budget rows match, and must NEVER strip "-pass<N>"
    (binutils-pass1 is the calibration anchor's real name).
    """

    def _derive(self, basename):
        out = subprocess.run(
            ["bash", "-c",
             f'source <(sed -n "/^pkg_from_logname()/,/^}}/p" "{SCRIPT}"); '
             f'pkg_from_logname "{basename}"'],
            capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def test_ch8_marker_stripped(self):
        assert self._derive("gcc-ch8-20260709-231224.log") == "gcc"

    def test_chroot_marker_stripped(self):
        assert self._derive("gettext-chroot-20260709-222438.log") == "gettext"

    def test_ch10_marker_stripped(self):
        assert self._derive("linux-kernel-ch10-20260710-010101.log") == "linux-kernel"

    def test_pass_names_never_stripped(self):
        assert self._derive("binutils-pass1-20260709-214600.log") == "binutils-pass1"
        assert self._derive("gcc-pass2-20260709-221324.log") == "gcc-pass2"

    def test_python_tier_name_unchanged(self):
        assert self._derive("webkitgtk-20260704-120000.log") == "webkitgtk"

    def test_core_extra_marker_stripped(self):
        assert self._derive("cryptsetup-static-core-extra-20260710-011500.log") == "cryptsetup-static"

    def test_base_marker_stripped_but_not_name_fragments(self):
        assert self._derive("htop-base-20260710-020000.log") == "htop"
        # "-files" is not a marker; compound names survive
        assert self._derive("intergenos-base-files-ch8-20260709-230000.log") == "intergenos-base-files"


class TestFirstSeenKeying:
    """Live-defect #4 (2026-07-10): first_seen must key by LOG BASENAME, not the
    derived pkg name — bare names repeat across phases (Ch7 ncurses rebuilds in
    Ch8), and a name-keyed clock inherits the earlier phase's epoch."""

    def test_same_pkg_different_logs_get_independent_clocks(self, tmp_path):
        state = tmp_path / "state"
        script = (
            f'STATE_FILE="{state}"; '
            f'source <(sed -n "/^now_epoch()/,/^}}/p" "{SCRIPT}"); '
            f'source <(sed -n "/^first_seen()/,/^}}/p" "{SCRIPT}"); '
            # phase 1: the Ch7 attempt is recorded with an OLD epoch
            f'printf "ncurses-chroot-20260709-220600.log\\t1000000\\n" > "$STATE_FILE"; '
            # phase 2: the Ch8 attempt must get a FRESH clock, not 1000000
            f'first_seen "ncurses-ch8-20260710-000900.log"'
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() != "1000000"
        assert int(out.stdout.strip()) > 1_700_000_000
        # and the ch7 row is untouched / still resolvable
        text = state.read_text()
        assert "ncurses-chroot-20260709-220600.log\t1000000" in text