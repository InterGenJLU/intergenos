# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""installer/smoke — sign/module-sig-force must read a large kernel config
under the harness's own shell options.

WHAT THIS PINS. smoke-test.sh runs every check under `set -uo pipefail`.
When /proc/sys/kernel/module_sig_enforce is absent (the sysctl is compiled
out when CONFIG_MODULE_SIG_FORCE=y, the strongest posture), the check reads
the kernel config to tell "enforcement compiled in" from "no enforcement".
It piped the whole config into `grep -q`; grep exits at the first match, the
writer takes SIGPIPE on the rest (a shipped config is ~300 KB and the line
sits near the top), and under pipefail the pipeline reports failure — so a
kernel with enforcement compiled in was reported as NOT enforcing.

Measured: the R001.2 pre-install evaluation of 2026-08-27 and again of
2026-09-02 (unpriv/07-smoke-harness.txt) — FAIL sign/module-sig-force while
/boot/config-6.18.10-igos-21 line 1051 read CONFIG_MODULE_SIG_FORCE=y;
reproduced 5/5 against that config with PIPESTATUS "141 0".

The test drives the real check function under the real options against a
config of the shipped size with the line where the shipped one has it.
"""

import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
LIB_SH = SMOKE_DIR / "lib.sh"
SIGNING_SH = SMOKE_DIR / "checks" / "signing.sh"


def _shipped_size_config(tmp_path, enforce_line):
    """~300 KB of config text; the enforcement line about 1,000 lines in."""
    lines = [f"CONFIG_FILLER_{i}=y" for i in range(1000)]
    lines.append(enforce_line)
    lines.extend(f"# CONFIG_FILLER_TAIL_{i} is not set" for i in range(9000))
    p = tmp_path / "config-test"
    p.write_text("\n".join(lines) + "\n")
    assert p.stat().st_size > 250_000
    return p


def _run_check(tmp_path, kconfig):
    script = textwrap.dedent(f"""
        set -uo pipefail
        SMOKE_JSON=1
        SMOKE_MODULE_SIG_ENFORCE="{tmp_path}/no-such-sysctl"
        SMOKE_KCONFIG="{kconfig}"
        . "{LIB_SH}"
        . "{SIGNING_SH}"
        check_signing_module_sig_force
        for r in "${{SMOKE_RESULTS[@]}}"; do printf '%s\\n' "$r"; done
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = [line for line in r.stdout.splitlines() if "|" in line]
    return [row for row in rows if row.split("|")[1] == "sign/module-sig-force"]


def test_compiled_in_enforcement_passes_under_pipefail(tmp_path):
    cfg = _shipped_size_config(tmp_path, "CONFIG_MODULE_SIG_FORCE=y")
    rows = _run_check(tmp_path, cfg)
    assert rows, "the check produced no verdict"
    assert rows[0].split("|")[0] == "PASS", rows
    assert "BY DESIGN" in rows[0]


def test_enforcement_not_compiled_in_still_fails(tmp_path):
    """The fix must not silence the real negative."""
    cfg = _shipped_size_config(tmp_path, "# CONFIG_MODULE_SIG_FORCE is not set")
    rows = _run_check(tmp_path, cfg)
    assert rows and rows[0].split("|")[0] == "FAIL", rows


def test_no_config_at_all_is_a_skip(tmp_path):
    rows = _run_check(tmp_path, tmp_path / "absent-config")
    assert rows and rows[0].split("|")[0] == "SKIP", rows
