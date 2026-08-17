#!/bin/bash
# Test runner for check-public-content.py
# Asserts that should-fail fixtures produce violations and should-pass produce none.

set -euo pipefail

SCRIPT="$(dirname "$0")/../../scripts/check-public-content.py"
SHOULD_FAIL_DIR="$(dirname "$0")/should-fail"
SHOULD_PASS_DIR="$(dirname "$0")/should-pass"

PASS=0
FAIL=0

echo "=== Testing should-fail fixtures ==="
for fixture in "$SHOULD_FAIL_DIR"/*; do
    if [ ! -f "$fixture" ]; then
        continue
    fi
    basename="$(basename "$fixture")"
    if python3 "$SCRIPT" --file "$fixture" --require-fail > /dev/null 2>&1; then
        echo "  PASS: $basename (violations detected as expected)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $basename (expected violations, got clean)"
        python3 "$SCRIPT" --file "$fixture" 2>&1 || true
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== Testing should-pass fixtures ==="
for fixture in "$SHOULD_PASS_DIR"/*; do
    if [ ! -f "$fixture" ]; then
        continue
    fi
    basename="$(basename "$fixture")"
    if python3 "$SCRIPT" --file "$fixture" --require-clean > /dev/null 2>&1; then
        echo "  PASS: $basename (clean as expected)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $basename (expected clean, got violations)"
        python3 "$SCRIPT" --file "$fixture" 2>&1 || true
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== Testing commit-message audit fail-closed behavior ==="
# An unresolvable range must FAIL, not report PASS: before the fail-closed
# fix the script printed an ERROR line and then exited 0 with "PASS: no
# violations found", and the pre-push hook's gate 7 read that as a pass —
# an audit that checked nothing reporting success.
if python3 "$SCRIPT" --commit-msgs "definitely-not-a-ref..HEAD" > /dev/null 2>&1; then
    echo "  FAIL: unresolvable range exited 0 (fail-open — the audit ran nothing and reported PASS)"
    FAIL=$((FAIL + 1))
else
    echo "  PASS: unresolvable range fails closed"
    PASS=$((PASS + 1))
fi
# Control: a resolvable-but-empty range is a legitimately clean audit and
# must still pass — fail-closed must not become fail-always.
if python3 "$SCRIPT" --commit-msgs "HEAD..HEAD" > /dev/null 2>&1; then
    echo "  PASS: empty valid range passes"
    PASS=$((PASS + 1))
else
    echo "  FAIL: empty valid range did not pass"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Testing --file path-containment refusal ==="
# A --file path outside the repository root must be REFUSED with a named
# message and exit 2, never a traceback and never exit 1. Two shapes used to
# fail differently and both badly: an existing out-of-repo path crashed with
# an unhandled ValueError (exit 1 — the same status as "violations found"),
# and a NON-existing one was skipped and reported "PASS: no violations found"
# with exit 0, which is a scan of nothing presented as clean.
REPO_ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SHOULD_PASS_REL="tests/check-public-content/should-pass"
SHOULD_FAIL_REL="tests/check-public-content/should-fail"
OUTSIDE_EXISTING="$(mktemp)"
trap 'rm -f "$OUTSIDE_EXISTING"' EXIT
printf 'ordinary text, no violations here\n' > "$OUTSIDE_EXISTING"
OUTSIDE_MISSING="${OUTSIDE_EXISTING}.does-not-exist"

check_refusal() {
    # $1 = human label, $2 = path handed to --file, $3 = the phrase the
    # refusal message must contain, $4.. = extra scanner args
    #
    # $3 is not decoration. Several distinct refusal conditions live in one
    # function and each later check would also catch an input the earlier one
    # was written for — a directory is also "not a regular file", a missing
    # path is also "not a regular file", a ref lookup that fails also yields a
    # non-blob type. Asserting only "it refused" therefore passed even with
    # the specific check deleted (measured: three mutations survived exactly
    # this way). Pinning the REASON is what makes each assertion test its own
    # condition, and what keeps the message a caller reads accurate.
    local label="$1" path="$2" expect="$3"; shift 3
    local out rc
    # rc is captured through the && / || list so `set -e` does not abort the
    # run on the non-zero exit that is precisely what this test asserts.
    #
    # Every probe runs under `timeout`: one of the inputs asserted below is a
    # named pipe, and before the readability refusal landed, opening it
    # blocked forever. Without the timeout a regression would HANG this suite
    # instead of failing it, and a suite that hangs reports nothing at all.
    out="$(timeout 20 python3 "$SCRIPT" --file "$path" "$@" 2>&1)" && rc=0 || rc=$?
    if [ "$rc" -eq 124 ]; then
        echo "  FAIL: $label (timed out after 20s — the scanner did not return)"
        FAIL=$((FAIL + 1))
        return
    fi
    if [ "$rc" -ne 2 ]; then
        echo "  FAIL: $label (expected exit 2, got $rc)"
        echo "$out" | sed 's/^/        /'
        FAIL=$((FAIL + 1))
        return
    fi
    if ! printf '%s' "$out" | grep -q 'REFUSED'; then
        echo "  FAIL: $label (exit 2 but no REFUSED message)"
        FAIL=$((FAIL + 1))
        return
    fi
    # The message must NAME the offending path — a refusal that does not say
    # which input it refused sends the reader back to guessing.
    if ! printf '%s' "$out" | grep -qF "$path"; then
        echo "  FAIL: $label (refusal does not name the path)"
        FAIL=$((FAIL + 1))
        return
    fi
    if printf '%s' "$out" | grep -q 'Traceback (most recent call last)'; then
        echo "  FAIL: $label (printed a traceback)"
        FAIL=$((FAIL + 1))
        return
    fi
    if ! printf '%s' "$out" | grep -qF "$expect"; then
        echo "  FAIL: $label (refused for the wrong reason — expected the message to say \"$expect\")"
        echo "$out" | sed 's/^/        /'
        FAIL=$((FAIL + 1))
        return
    fi
    echo "  PASS: $label (refused, exit 2, named, correct reason, no traceback)"
    PASS=$((PASS + 1))
}

check_refusal "absolute out-of-repo path that EXISTS" "$OUTSIDE_EXISTING" \
    "outside the repository root"
check_refusal "absolute out-of-repo path that DOES NOT exist" "$OUTSIDE_MISSING" \
    "outside the repository root"
check_refusal "relative path escaping the repository root" "../$(basename "$OUTSIDE_EXISTING")" \
    "outside the repository root"

# Controls: the fix must refuse only what is outside. An in-repo path still
# scans normally by either spelling, so fail-closed did not become fail-always.
if python3 "$SCRIPT" --file "$REPO_ROOT_DIR/$SHOULD_PASS_REL/clean-content.txt" --require-clean > /dev/null 2>&1; then
    echo "  PASS: absolute in-repo path still scans clean"
    PASS=$((PASS + 1))
else
    echo "  FAIL: absolute in-repo path no longer scans clean"
    FAIL=$((FAIL + 1))
fi
if (cd "$REPO_ROOT_DIR" && python3 "$SCRIPT" --file "$SHOULD_PASS_REL/clean-content.txt" --require-clean > /dev/null 2>&1); then
    echo "  PASS: relative in-repo path still scans clean"
    PASS=$((PASS + 1))
else
    echo "  FAIL: relative in-repo path no longer scans clean"
    FAIL=$((FAIL + 1))
fi
# Control: a should-fail fixture must still be DETECTED (exit 1), proving the
# refusal path did not swallow the detection path.
if python3 "$SCRIPT" --file "$REPO_ROOT_DIR/$SHOULD_FAIL_REL/agent-name.txt" --require-fail > /dev/null 2>&1; then
    echo "  PASS: in-repo violating fixture still detected"
    PASS=$((PASS + 1))
else
    echo "  FAIL: in-repo violating fixture no longer detected"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Testing --file readability refusal (in-repo paths) ==="
# Containment (above) decides WHERE a --file path may point. Readability
# decides whether it points at anything this scanner can actually read. Every
# shape below used to be SKIPPED and reported as "PASS: no violations found."
# at exit 0 — a scan of nothing presented as a clean result — because
# read_file_content turns a missing path into None and turns every OSError
# (IsADirectoryError included) into None as well.
SCRATCH="$REPO_ROOT_DIR/.check-public-content-readability-scratch"
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH/a-directory"
printf 'ordinary text, nothing to find here\n' > "$SCRATCH/real-file.txt"
ln -s "$SCRATCH/real-file.txt" "$SCRATCH/good-symlink.txt"
ln -s "$SCRATCH/no-such-target.txt" "$SCRATCH/broken-symlink.txt"
mkfifo "$SCRATCH/a-fifo"
SCRATCH_REL=".check-public-content-readability-scratch"
# Replace the earlier single-path trap so both scratch artifacts are removed
# however this script exits.
trap 'rm -f "$OUTSIDE_EXISTING"; rm -rf "$SCRATCH"' EXIT

# The expected phrase here carries the clause after the dash on purpose. A
# bare "does not exist" is ALSO a substring of the dangling-symlink refusal
# ("...whose target does not exist"), so with the missing-path check deleted
# the symlink branch answered instead and a bare-substring assertion still
# passed — measured, that mutation survived until this phrase was tightened.
check_refusal "in-repo path that does not exist (absolute)" "$SCRATCH/no-such-file.txt" \
    "does not exist — this audit will not report"
check_refusal "in-repo path that does not exist (relative)" "$SCRATCH_REL/no-such-file.txt" \
    "does not exist — this audit will not report"
check_refusal "in-repo path that is a DIRECTORY (absolute)" "$SCRATCH/a-directory" \
    "is a directory"
check_refusal "in-repo path that is a DIRECTORY (relative)" "$SCRATCH_REL/a-directory" \
    "is a directory"
check_refusal "the repository root itself, a directory"     "$REPO_ROOT_DIR" \
    "is a directory"
check_refusal "in-repo dangling symlink"                    "$SCRATCH/broken-symlink.txt" \
    "symbolic link whose target does not exist"
# The fifo is the reason check_refusal runs under `timeout`: opening it used
# to block forever, so this assertion guards against a hang, not just a wrong
# answer.
check_refusal "in-repo named pipe (must refuse, must not hang)" "$SCRATCH/a-fifo" \
    "is not a regular file"

# Controls: readability refusal must not become refuse-everything. A real
# file and a symlink that resolves to one are both readable and must scan.
if timeout 20 python3 "$SCRIPT" --file "$SCRATCH/real-file.txt" --require-clean > /dev/null 2>&1; then
    echo "  PASS: in-repo regular file still scans clean"
    PASS=$((PASS + 1))
else
    echo "  FAIL: in-repo regular file no longer scans clean"
    FAIL=$((FAIL + 1))
fi
if timeout 20 python3 "$SCRIPT" --file "$SCRATCH/good-symlink.txt" --require-clean > /dev/null 2>&1; then
    echo "  PASS: in-repo symlink to a real file still scans clean"
    PASS=$((PASS + 1))
else
    echo "  FAIL: in-repo symlink to a real file no longer scans clean"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Testing --file readability against a git ref (--from-ref) ==="
# With --from-ref the content comes from `git show <ref>:<path>`, so
# existence is decided against THAT ref, not the working tree. Two things are
# asserted here. First, a path absent from the ref is refused rather than
# reported clean. Second — the sharper one — an ABSOLUTE --file path must
# still be READ: `git show` resolves against the repository root, so handing
# it an absolute spelling made the read fail and the file get reported clean.
# That was a detection MISS, not merely an unread input: the same violating
# fixture reported 3 violations by its relative spelling and "PASS" by its
# absolute one.
check_refusal "path absent from the ref is refused" "$SHOULD_PASS_REL/no-such-file-in-the-ref.txt" \
    "does not exist in HEAD" --from-ref HEAD

if (cd "$REPO_ROOT_DIR" && timeout 20 python3 "$SCRIPT" --file "$SHOULD_PASS_REL/clean-content.txt" --from-ref HEAD --require-clean > /dev/null 2>&1); then
    echo "  PASS: relative in-repo path scans clean from the ref"
    PASS=$((PASS + 1))
else
    echo "  FAIL: relative in-repo path no longer scans clean from the ref"
    FAIL=$((FAIL + 1))
fi
if timeout 20 python3 "$SCRIPT" --file "$REPO_ROOT_DIR/$SHOULD_FAIL_REL/agent-name.txt" --from-ref HEAD --require-fail > /dev/null 2>&1; then
    echo "  PASS: ABSOLUTE path violating fixture detected from the ref"
    PASS=$((PASS + 1))
else
    echo "  FAIL: ABSOLUTE path violating fixture NOT detected from the ref (silent miss)"
    FAIL=$((FAIL + 1))
fi
if (cd "$REPO_ROOT_DIR" && timeout 20 python3 "$SCRIPT" --file "$SHOULD_FAIL_REL/agent-name.txt" --from-ref HEAD --require-fail > /dev/null 2>&1); then
    echo "  PASS: relative path violating fixture detected from the ref"
    PASS=$((PASS + 1))
else
    echo "  FAIL: relative path violating fixture no longer detected from the ref"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Testing --dir by BOTH spellings (absolute and relative) ==="
# --dir used to depend on how the caller spelled the path. The walk started
# from the caller's spelling while the scan root was the RESOLVED absolute
# path, so a relative --dir produced paths that were joined onto the root a
# second time; every file resolved to a doubled path that does not exist,
# read as None, and was skipped. The whole scan then reported clean having
# read nothing. Absolute spellings were unaffected, which is why it survived.
#
# The fixture below is a copy of this suite's own agent-name should-fail
# fixture, which carries three BLOCK violations. Both spellings must DETECT.
DIRSCRATCH="$REPO_ROOT_DIR/.check-public-content-dir-scratch"
rm -rf "$DIRSCRATCH"
mkdir -p "$DIRSCRATCH/violating" "$DIRSCRATCH/clean"
cp "$REPO_ROOT_DIR/$SHOULD_FAIL_REL/agent-name.txt" "$DIRSCRATCH/violating/copied-fixture.txt"
printf 'ordinary text, nothing to find here\n' > "$DIRSCRATCH/clean/clean-file.txt"
DIRSCRATCH_REL=".check-public-content-dir-scratch"
trap 'rm -f "$OUTSIDE_EXISTING"; rm -rf "$SCRATCH"; rm -rf "$DIRSCRATCH"' EXIT

check_dir_detects() {
    # $1 = label, $2 = --dir argument, $3 = cwd to run from
    local label="$1" dirarg="$2" wd="$3" out rc
    out="$(cd "$wd" && timeout 60 python3 "$SCRIPT" --dir "$dirarg" 2>&1)" && rc=0 || rc=$?
    if [ "$rc" -ne 1 ]; then
        echo "  FAIL: $label (expected exit 1 for a violating tree, got $rc)"
        echo "$out" | sed 's/^/        /'
        FAIL=$((FAIL + 1))
        return
    fi
    # Exit 1 alone is not enough: the count proves the file was actually READ,
    # not merely that something failed.
    if ! printf '%s' "$out" | grep -q 'BLOCK violations: 3'; then
        echo "  FAIL: $label (exit 1 but did not report the 3 known violations)"
        echo "$out" | sed 's/^/        /'
        FAIL=$((FAIL + 1))
        return
    fi
    echo "  PASS: $label (3 BLOCK violations detected, exit 1)"
    PASS=$((PASS + 1))
}

check_dir_clean() {
    # $1 = label, $2 = --dir argument, $3 = cwd to run from
    local label="$1" dirarg="$2" wd="$3"
    if (cd "$wd" && timeout 60 python3 "$SCRIPT" --dir "$dirarg" --require-clean > /dev/null 2>&1); then
        echo "  PASS: $label (clean tree scans clean)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (clean tree did not scan clean)"
        FAIL=$((FAIL + 1))
    fi
}

check_dir_detects "ABSOLUTE --dir path detects the violating tree" \
    "$DIRSCRATCH/violating" "$REPO_ROOT_DIR"
check_dir_detects "RELATIVE --dir path detects the violating tree" \
    "$DIRSCRATCH_REL/violating" "$REPO_ROOT_DIR"
# A relative spelling that is not simply a child of the cwd must work too,
# so the fix cannot be a cwd coincidence.
check_dir_detects "RELATIVE --dir path with a ./ prefix detects the violating tree" \
    "./$DIRSCRATCH_REL/violating" "$REPO_ROOT_DIR"

# Controls: the fix must not turn every --dir scan into a detection.
check_dir_clean "ABSOLUTE --dir path on a clean tree" "$DIRSCRATCH/clean" "$REPO_ROOT_DIR"
check_dir_clean "RELATIVE --dir path on a clean tree" "$DIRSCRATCH_REL/clean" "$REPO_ROOT_DIR"

# Control: --dir naming something that is not a directory is still refused at
# exit 2 by both spellings, and still names the path as the caller typed it.
for spelling in "$DIRSCRATCH/clean/clean-file.txt" "$DIRSCRATCH_REL/clean/clean-file.txt"; do
    out="$(cd "$REPO_ROOT_DIR" && timeout 60 python3 "$SCRIPT" --dir "$spelling" 2>&1)" && rc=0 || rc=$?
    if [ "$rc" -eq 2 ] && printf '%s' "$out" | grep -qF "not a directory: $spelling"; then
        echo "  PASS: --dir on a regular file refused at exit 2, path named as typed ($spelling)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: --dir on a regular file (expected exit 2 naming the path as typed, got $rc)"
        echo "$out" | sed 's/^/        /'
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
