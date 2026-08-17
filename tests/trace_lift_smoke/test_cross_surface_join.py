# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Cross-surface smoke test for the build-pipeline debug-logger lift (Steps 1-11).

This test verifies the lift's load-bearing cross-file jq-join property by
running synthetic invocations across all four surface types — chroot-build
(bash), pkm (Python), igos-build (Python), and the orchestrator (bash) —
under a shared IGOS_TRACE_RUNID and asserting:

  1. Every surface emits to the canonical
     `build-<scope>-<startts>-<runid>.jsonl` path under IGOS_TRACE_ROOT.
  2. Every event line is valid JSON with type + ts.
  3. The four surfaces share the same <startts>-<runid> suffix family so
     `cat build-*-<startts>-<runid>.jsonl | jq` joins them cleanly.
  4. The schema vocabulary is consistent across surfaces (the dossier's
     30-lift-plan.md section 2 canonical event types appear in the joined
     output).
  5. The build_summary index file lists every emitted JSONL file.

This is the "Forge install-#28 was a 5-minute jq query" property — the
build pipeline now offers the same.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def run_synthetic_lift():
    """Run synthetic invocations across all four surface types and return
    (trace_root, startts, runid, summary_path)."""
    trace_root = tempfile.mkdtemp(prefix="trace-lift-smoke-")
    startts = "20260528T235959Z"
    runid = "abcdef1234567890"

    env = os.environ.copy()
    env.update({
        "IGOS_BUILD_DEBUG_VERBOSE": "1",
        "IGOS_TRACE_ROOT": trace_root,
        "IGOS_TRACE_RUNID": runid,
        "IGOS_TRACE_START_TS": startts,
    })

    # Surface 1: bash chroot-build-style (sources trace.sh + emits tier events)
    bash_script_1 = """
        set -e
        source "$REPO_ROOT/scripts/lib/trace.sh"
        trace_init "tier-test"
        trace_event tier_start tier=test log_file=/tmp/fake.log
        trace_pkg_enter glibc 2.43 test
        trace_pkg_phase glibc configure 0 100
        trace_run --pkg glibc --phase build --intent "synthetic build step" -- \
            sh -c 'echo "build stdout"; echo "build stderr" >&2; exit 0'
        trace_pkg_exit glibc 0 250
        trace_event tier_end tier=test rc::=0 duration_ms::=300
        trace_close
    """
    subprocess.run(
        ["bash", "-c", bash_script_1],
        env={**env, "REPO_ROOT": str(REPO_ROOT)},
        check=True,
    )

    # Surface 2: Python igos-build-style (Python emits via _trace shim)
    py_igos_build = f"""
import sys, importlib.util
sys.path.insert(0, '{REPO_ROOT}')
spec = importlib.util.spec_from_file_location('ib_t', '{REPO_ROOT}/igos-build/_trace.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.init_build_trace()
m.trace_event('build_start', runid=m.get_runid(), source='igos-build', argv=['--smoke'])
m.trace_event('pkg_enter', pkg='hello-py', version='1.0', tier='igos-build', style='make')
m.trace_event('subprocess_output', pkg='hello-py',
              text='[CONFIG] checking compiler...', bytes=30)
m.trace_event('pkg_phase', pkg='hello-py', phase='configure', rc=0, duration_ms=42)
m.trace_event('pkg_exit', pkg='hello-py', rc=0, duration_ms=100)
m.trace_event('build_end', success=True, source='igos-build')
m.close_trace()
"""
    subprocess.run(
        [sys.executable, "-c", py_igos_build],
        env=env,
        check=True,
    )

    # Surface 3: Python pkm-style
    py_pkm = f"""
import sys, importlib.util
sys.path.insert(0, '{REPO_ROOT}')
spec = importlib.util.spec_from_file_location('pkm_t', '{REPO_ROOT}/pkm/_trace.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.init_package_trace('pkm-install', phase='install')
m.trace_event('pkm_invoke', subcommand='install', argv=['install', 'hello'], cwd='/')
m.trace_event('pkm_lock_acquired', command='install', lock_path='/var/lock/pkm.lock')
m.trace_event('pkm_db_write', operation='add_installed', pkg='hello',
              version='1.0', tier='extra', install_method='archive',
              install_reason='manual')
m.trace_event('pkm_hook_fire', pkg='hello', hook='post_install')
m.trace_event('pkm_hook_done', pkg='hello', hook='post_install', rc=0,
              duration_ms=1234)
m.trace_event('pkm_lock_released', command='install', lock_path='/var/lock/pkm.lock')
m.close_trace()
"""
    subprocess.run(
        [sys.executable, "-c", py_pkm],
        env=env,
        check=True,
    )

    # Surface 4: bash orchestrator-style (mirrors emit_build_summary)
    bash_orch = """
        set -e
        source "$REPO_ROOT/scripts/lib/trace.sh"
        trace_init "orchestrator" "$IGOS_TRACE_RUNID"
        trace_event build_start runid="$IGOS_TRACE_RUNID" host=test-host \
            image_user=christopher \
            phase_list::="[\\"validate\\",\\"setup\\"]"
        trace_phase_enter validate "Verify host requirements"
        trace_phase_exit validate 0 500
        trace_event build_end success::=true elapsed_s::=600 last_phase=iso

        # Emit a synthetic build_summary linking every JSONL file
        trace_files=$(find "$IGOS_TRACE_ROOT" -maxdepth 1 -type f \
                        -name "*-${IGOS_TRACE_START_TS}-${IGOS_TRACE_RUNID}.jsonl" \
                        -printf '%f\\n' | sort | jq -R . | jq -s -c .)
        summary_path="${IGOS_TRACE_ROOT}/build-summary-${IGOS_TRACE_START_TS}-${IGOS_TRACE_RUNID}.json"
        jq -n -c \
            --arg runid "$IGOS_TRACE_RUNID" \
            --arg start_ts "$IGOS_TRACE_START_TS" \
            --argjson trace_files "$trace_files" \
            '{runid:$runid, start_ts:$start_ts, trace_files:$trace_files,
              success:true, elapsed_s:600}' \
            > "$summary_path"
        trace_event build_summary_emit summary_path="$summary_path" \
            trace_file_count::=$(echo "$trace_files" | jq 'length')
        trace_close
    """
    subprocess.run(
        ["bash", "-c", bash_orch],
        env={**env, "REPO_ROOT": str(REPO_ROOT)},
        check=True,
    )

    summary_path = Path(trace_root) / f"build-summary-{startts}-{runid}.json"
    return trace_root, startts, runid, summary_path


def test_all_four_surfaces_emit_jsonl():
    """All four surfaces (chroot-build bash, igos-build py, pkm py,
    orchestrator bash) emit to the canonical naming convention."""
    trace_root, startts, runid, summary_path = run_synthetic_lift()
    try:
        files = list(Path(trace_root).glob(f"*-{startts}-{runid}.jsonl"))
        names = sorted(f.name for f in files)

        # Must have at least one file per surface. Names look like:
        #   build-tier-test-<startts>-<runid>.jsonl       (chroot-build)
        #   build-orchestrator-<startts>-<runid>.jsonl    (orchestrator AND igos-build init_build_trace)
        #   build-pkg-pkm-install-<startts>-<runid>.jsonl (pkm init_package_trace)
        has_tier = any("build-tier-" in n for n in names)
        has_orchestrator = any("build-orchestrator-" in n for n in names)
        has_pkg = any("build-pkg-" in n for n in names)

        assert has_tier, f"missing chroot-build tier sink — got: {names}"
        assert has_orchestrator, f"missing orchestrator sink — got: {names}"
        assert has_pkg, f"missing pkm/igos-build pkg sink — got: {names}"

    finally:
        shutil.rmtree(trace_root, ignore_errors=True)


def test_cross_file_jq_join_works():
    """Concatenating every JSONL in the runid family yields a single valid
    JSON-line stream parseable by jq — the load-bearing cross-file join
    property the lift is designed to enable."""
    trace_root, startts, runid, summary_path = run_synthetic_lift()
    try:
        files = sorted(Path(trace_root).glob(f"*-{startts}-{runid}.jsonl"))
        all_events = []
        for f in files:
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                assert "type" in event, f"event missing 'type': {event}"
                assert "ts" in event, f"event missing 'ts': {event}"
                all_events.append(event)

        # Expected event types across all surfaces — the canonical vocabulary
        # from dossier 30-lift-plan.md section 2
        types_seen = {e["type"] for e in all_events}
        expected_types = {
            "trace_init",         # every sink opens with this
            "tier_start", "tier_end",   # chroot-build surface
            "pkg_enter", "pkg_exit", "pkg_phase",  # bash + python pkg events
            "subprocess_start", "subprocess_end",  # trace_run + traced_run
            "subprocess_output",          # igos-build per-line stream events
            "build_start", "build_end",   # orchestrator + igos-build entry/exit
            "phase_enter", "phase_exit",  # orchestrator phase boundary
            "pkm_invoke",                 # pkm cli surface
            "pkm_lock_acquired", "pkm_lock_released",
            "pkm_db_write",
            "pkm_hook_fire", "pkm_hook_done",
            "build_summary_emit",
        }
        missing = expected_types - types_seen
        assert not missing, (
            f"Cross-surface schema vocabulary missing types: {missing}. "
            f"Saw: {sorted(types_seen)}"
        )

    finally:
        shutil.rmtree(trace_root, ignore_errors=True)


def test_build_summary_lists_every_trace_file():
    """The build_summary JSON lists every JSONL emitted in this runid
    family — closes the "given the build, find every trace" loop."""
    trace_root, startts, runid, summary_path = run_synthetic_lift()
    try:
        assert summary_path.exists(), (
            f"build_summary not emitted at {summary_path}"
        )
        summary = json.loads(summary_path.read_text())
        assert summary["runid"] == runid
        assert summary["start_ts"] == startts
        assert "trace_files" in summary
        trace_files = summary["trace_files"]
        assert len(trace_files) >= 3, (
            f"summary trace_files count too low: {len(trace_files)} "
            f"({trace_files})"
        )
        # Every file in trace_files must actually exist
        for fname in trace_files:
            assert (Path(trace_root) / fname).exists(), (
                f"summary lists {fname} but file does not exist"
            )

    finally:
        shutil.rmtree(trace_root, ignore_errors=True)


def test_iso_timestamps_match_between_python_and_bash():
    """ISO-8601 millisecond timestamp format must match byte-for-byte between
    Python (igos_trace._iso_ts) and bash (trace.sh:_trace_iso_ts) so cross-file
    jq join by `.ts` works uniformly."""
    trace_root, startts, runid, summary_path = run_synthetic_lift()
    try:
        import re
        ts_pattern = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")
        files = list(Path(trace_root).glob(f"*-{startts}-{runid}.jsonl"))
        for f in files:
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                ts = event.get("ts", "")
                assert ts_pattern.match(ts), (
                    f"Event in {f.name} has non-canonical ts format: '{ts}' "
                    f"— Python<->bash cross-file jq join may break."
                )
    finally:
        shutil.rmtree(trace_root, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    tests = [
        test_all_four_surfaces_emit_jsonl,
        test_cross_file_jq_join_works,
        test_build_summary_lists_every_trace_file,
        test_iso_timestamps_match_between_python_and_bash,
    ]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
        except Exception:
            print(f"  FAIL: {t.__name__}")
            traceback.print_exc()
            fails += 1
    if fails:
        print(f"\nCross-surface smoke: {fails}/{len(tests)} tests failed")
        sys.exit(1)
    print(f"\nCross-surface smoke: {len(tests)}/{len(tests)} tests passed")
    sys.exit(0)
