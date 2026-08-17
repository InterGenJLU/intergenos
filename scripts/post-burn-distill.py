#!/usr/bin/env python3
# post-burn-distill.py — stage 1 of the post-burn trace audit (the green-log sweep).
#
# A build whose packages all compiled is NOT a build that is known-good — and a
# log the gates passed is NOT a log with nothing in it. This tool reads the
# forensic-trace JSONLs + host logs for an explicit build window, extracts
# pattern-class anomaly candidates from the SUCCESSFUL output (tolerated errors,
# silent fallbacks, silently-disabled features, swallowed test failures),
# dedupes them by normalized signature, and emits judgment-ready per-class
# bundles for independent review. Suppressions are COUNTED AND LABELED, never
# silent.
#
# Every record is tagged with the OUTCOME of the run that produced it
# (green / halted / unknown), derived from each trace file's terminal records —
# a corpus spanning a real build arc contains logs from runs that later halted
# and were remediated, and a halt's own failure lines must be separable from a
# finding that survived into the shipped artifact.
#
# Usage:
#   post-burn-distill.py --since 20260710T0230 --until 20260711T1120 \
#       [--trace-root DIR] [--host-logs DIR] [--out DIR] [--bundle-cap BYTES]
#
#   --trace-root defaults to $IGOS_TRACE_ROOT (required if the env is unset)
#   --host-logs  defaults to <repo>/build/logs
#   --out        defaults to ./post-burn-sweep (creates out/ and bundles/)
#
# First proven on the ge9b-01 burn sweep (2026-07-11). Methodology + the
# judgment stages this feeds: the post-burn trace-audit process doc.
import argparse
import glob
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

ap = argparse.ArgumentParser(description="Distill anomaly candidates from green build logs")
ap.add_argument("--since", required=True, help="window start, YYYYMMDDTHHMM (UTC, matches trace filenames)")
ap.add_argument("--until", required=True, help="window end, YYYYMMDDTHHMM (UTC)")
ap.add_argument("--trace-root", default=os.environ.get("IGOS_TRACE_ROOT"),
                help="forensic trace root (default: $IGOS_TRACE_ROOT)")
ap.add_argument("--host-logs", default=None,
                help="host build-log dir (default: <repo>/build/logs)")
ap.add_argument("--out", default="./post-burn-sweep",
                help="output dir; creates out/ and bundles/ under it")
ap.add_argument("--bundle-cap", type=int, default=250000,
                help="max bytes per judge bundle file (full class files stay in out/)")
args = ap.parse_args()

if not args.trace_root:
    sys.exit("ERROR: no trace root — pass --trace-root or set IGOS_TRACE_ROOT")
TRACE = args.trace_root
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTLOGS = args.host_logs or os.path.join(REPO, "build", "logs")
OUT = os.path.join(os.path.abspath(args.out), "out")
BUNDLES = os.path.join(os.path.abspath(args.out), "bundles")
os.makedirs(OUT, exist_ok=True)
os.makedirs(BUNDLES, exist_ok=True)

WIN_LO, WIN_HI = args.since, args.until

def in_window(path):
    m = re.search(r"(\d{8}T\d{6})Z", path)
    if not m:
        return False
    return WIN_LO <= m.group(1)[:13] <= WIN_HI

# ---- pattern classes (order matters: first match wins per line) ----
# The three suppressed-* classes are known-benign wall classes (vendored-Go
# tarball builds, headless-chroot display failures, configure probe chatter).
# They are counted and reported, never silently dropped.
CLASSES = [
    ("suppressed-not-git",   re.compile(r"fatal: not a git repository"), True),
    ("suppressed-display",   re.compile(r"Failed to open display|cannot open display|Gtk-WARNING.*display|failed to open X display", re.I), True),
    ("suppressed-cfg-probe", re.compile(r"^checking\b.*\.\.\. n?o?$|^checking (for|whether|if)\b"), True),
    ("error",      re.compile(r"\berror\b[:!]|\bERROR\b|\bfailed\b.*\b(exit|status|code)\b|Traceback \(most recent", re.I), False),
    ("test-fail",  re.compile(r"^(FAIL|XFAIL|ERROR):|\bfailures?=\d+|\btests? failed\b|# fail:?\s*[1-9]", re.I), False),
    ("fallback",   re.compile(r"falling back|fall(s)? back to|fallback (to|enabled)|using bundled|bundled (copy|version)|vendored copy|subproject.*(fallback|download)", re.I), False),
    ("disabled",   re.compile(r"\b(disabled|will not be built|not being built|feature .{0,40}: ?no\b|support: ?no\b)", re.I), False),
    ("missing",    re.compile(r"\b(not found|no such file|missing|could not find|unable to find|cannot find)\b", re.I), False),
    ("deprecated", re.compile(r"\bdeprecat", re.I), False),
    ("skipped",    re.compile(r"\bskipp(ed|ing)\b|\bSKIP\b", re.I), False),
]
WARN_RE = re.compile(r"\bwarning\b", re.I)

def sig_of(line):
    # normalize: strip numbers, hex, paths -> stable class signature
    s = re.sub(r"/[^\s:]+", "/PATH", line.strip())
    s = re.sub(r"0x[0-9a-fA-F]+", "HEX", s)
    s = re.sub(r"\d+", "N", s)
    return s[:240]

class Bucket:
    __slots__ = ("count", "pkgs", "sample", "outcomes")
    def __init__(self):
        self.count = 0
        self.pkgs = set()
        self.sample = None
        self.outcomes = defaultdict(int)   # run outcome -> hit count

buckets = {name: defaultdict(Bucket) for name, _, _ in CLASSES}
suppressed_counts = defaultdict(int)
warn_sigs = defaultdict(Bucket)
pkg_rows = []          # (pkg, tier, version, rc, duration_ms, outcome, counts-per-class, warn_count)
seen_capture = set()   # dedupe identical captures across duplicate/resumed files

# ---- run-outcome derivation (per trace file / host log) ----
# A file's records are tagged green only when its terminal evidence says the
# run ended clean: any build_failure record, or a final rc != 0, marks the
# whole file halted. Judges read the tag to separate a remediated halt's own
# failure lines from findings that rode a green run into the artifact.
def trace_outcome(path):
    saw_failure = False
    last_rc = None
    with open(path, errors="replace") as f:
        for raw in f:
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "build_failure":
                saw_failure = True
            if "rc" in r and r.get("rc") is not None:
                last_rc = r.get("rc")
    if saw_failure:
        return "halted"
    if last_rc is None:
        return "unknown"
    return "green" if str(last_rc) == "0" else "halted"

HOSTLOG_FAIL = re.compile(r"BUILD FAILED|build_failure|✗ .* failed|FULL HALT", re.I)
HOSTLOG_OK = re.compile(r"stop-after|completed successfully|✓ .* built|exit(ing)? 0", re.I)

def hostlog_outcome(text):
    tail = text[-4000:]
    if HOSTLOG_FAIL.search(tail):
        return "halted"
    if HOSTLOG_OK.search(tail):
        return "green"
    return "unknown"

def scan_text(pkg, tier, text, outcome):
    counts = defaultdict(int)
    warn_count = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        matched = False
        for name, rx, is_suppress in CLASSES:
            if rx.search(line):
                matched = True
                if is_suppress:
                    suppressed_counts[name] += 1
                else:
                    counts[name] += 1
                    b = buckets[name][sig_of(line)]
                    b.count += 1
                    b.pkgs.add(pkg)
                    b.outcomes[outcome] += 1
                    if b.sample is None:
                        b.sample = f"[{tier}/{pkg}|{outcome}] {line.strip()[:300]}"
                break
        if not matched and WARN_RE.search(line):
            warn_count += 1
            b = warn_sigs[sig_of(line)]
            b.count += 1
            b.pkgs.add(pkg)
            b.outcomes[outcome] += 1
            if b.sample is None:
                b.sample = f"[{tier}/{pkg}|{outcome}] {line.strip()[:300]}"
    return counts, warn_count

BUILD_MARK = re.compile(r"^==> Building (\S+) (\S+)\s*$", re.M)
BUILT_RE = re.compile(r"^==> ✓ (\S+) built in ([\d.]+)s", re.M)

def handle_subprocess_end(r, tier, outcome):
    # Python tiers: one builder process, whole tier's stdout inline.
    # Split into per-package segments on the '==> Building <pkg> <ver>' marker.
    out = r.get("stdout", "")
    n = 0
    marks = [(m.start(), m.group(1), m.group(2)) for m in BUILD_MARK.finditer(out)]
    segments = []
    if marks:
        segments.append(("(tier-preamble)", "-", out[:marks[0][0]]))
        for i, (pos, pkg, ver) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(out)
            segments.append((pkg, ver, out[pos:end]))
    else:
        segments.append(("(tier-stream)", "-", out))
    for pkg, ver, seg in segments:
        key = hashlib.sha256((tier + pkg + str(len(seg)) + seg[:200]).encode()).hexdigest()
        if key in seen_capture:
            continue
        seen_capture.add(key)
        n += 1
        m = BUILT_RE.search(seg)
        dur = int(float(m.group(2)) * 1000) if m and m.group(1) == pkg else 0
        counts, warn_count = scan_text(pkg, tier, seg, outcome)
        pkg_rows.append((pkg, tier, ver, 0, dur, outcome, dict(counts), warn_count))
    err = r.get("stderr", "")
    if err.strip():
        counts, warn_count = scan_text("(tier-stderr)", tier, err, outcome)
        pkg_rows.append(("(tier-stderr)", tier, "-", r.get("rc"), 0, outcome, dict(counts), warn_count))
    return n

files = sorted(set(
    p for p in glob.glob(os.path.join(TRACE, "build-*.jsonl")) if in_window(p)
))
n_records = 0
n_segments = 0
outcome_by_file = {}
for path in files:
    outcome = trace_outcome(path)
    outcome_by_file[os.path.basename(path)] = outcome
    m = re.search(r"build-tier-([a-z0-9-]+)-\d{8}T", os.path.basename(path))
    file_tier = m.group(1) if m else "host"
    with open(path, errors="replace") as f:
        for raw in f:
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rtype = r.get("type")
            if rtype == "subprocess_end":
                # Only the tier-scoped streams: orchestrator/host streams embed
                # the tier output NESTED (double-count) — tier files are the
                # single source of truth for per-package output.
                if file_tier != "host":
                    n_segments += handle_subprocess_end(r, file_tier, outcome)
                continue
            if rtype != "pkg_capture":
                continue
            key = hashlib.sha256(
                (r.get("pkg", "?") + r.get("ts", "") + str(r.get("output_bytes", 0))).encode()
            ).hexdigest()
            if key in seen_capture:
                continue
            seen_capture.add(key)
            n_records += 1
            pkg, tier = r.get("pkg", "?"), r.get("tier", "?")
            counts, warn_count = scan_text(pkg, tier, r.get("output", ""), outcome)
            pkg_rows.append((pkg, tier, r.get("version", "?"), r.get("rc"),
                             r.get("duration_ms", 0), outcome, dict(counts), warn_count))

# host logs (plain text): toolchain per-pkg + orchestrator legs
for path in sorted(glob.glob(os.path.join(HOSTLOGS, "*.log"))):
    name = os.path.basename(path).rsplit("-", 2)[0]
    with open(path, errors="replace") as f:
        text = f.read()
    outcome = hostlog_outcome(text)
    outcome_by_file[os.path.basename(path)] = outcome
    counts, warn_count = scan_text(name, "hostlog", text, outcome)
    pkg_rows.append((name, "hostlog", "-", 0, 0, outcome, dict(counts), warn_count))

# ---- emit ----
def fmt_outcomes(oc):
    return ",".join(f"{k}={v}" for k, v in sorted(oc.items()))

with open(os.path.join(OUT, "summary.tsv"), "w") as f:
    f.write("pkg\ttier\tversion\trc\tduration_ms\trun_outcome\twarns\t" +
            "\t".join(n for n, _, s in CLASSES if not s) + "\n")
    for pkg, tier, ver, rc, dur, outcome, counts, wc in sorted(pkg_rows, key=lambda r: -(r[4] or 0)):
        f.write(f"{pkg}\t{tier}\t{ver}\t{rc}\t{dur}\t{outcome}\t{wc}\t" +
                "\t".join(str(counts.get(n, 0)) for n, _, s in CLASSES if not s) + "\n")

with open(os.path.join(OUT, "run-outcomes.txt"), "w") as f:
    for name, outcome in sorted(outcome_by_file.items()):
        f.write(f"{outcome}\t{name}\n")

for name in buckets:
    rows = sorted(buckets[name].items(), key=lambda kv: -kv[1].count)
    if not rows:
        continue
    with open(os.path.join(OUT, f"class-{name}.txt"), "w") as f:
        for sig, b in rows:
            f.write(f"count={b.count} pkgs={len(b.pkgs)} runs[{fmt_outcomes(b.outcomes)}] :: {b.sample}\n"
                    f"  sig: {sig}\n  pkgs: {', '.join(sorted(b.pkgs)[:20])}\n\n")

with open(os.path.join(OUT, "class-warnings.txt"), "w") as f:
    for sig, b in sorted(warn_sigs.items(), key=lambda kv: -kv[1].count)[:400]:
        f.write(f"count={b.count} pkgs={len(b.pkgs)} runs[{fmt_outcomes(b.outcomes)}] :: {b.sample}\n\n")

# judge bundles: byte-capped copies of each class file; the full files stay in out/.
# A capped bundle SAYS it is capped — silent truncation reads as full coverage.
for path in sorted(glob.glob(os.path.join(OUT, "class-*.txt"))):
    cname = os.path.basename(path)[len("class-"):-len(".txt")]
    size = os.path.getsize(path)
    dst = os.path.join(BUNDLES, f"bundle-{cname}.txt")
    with open(path, errors="replace") as f:
        data = f.read(args.bundle_cap)
    capped = size > args.bundle_cap
    with open(dst, "w") as f:
        if capped:
            f.write(f"# CAPPED at {args.bundle_cap} of {size} bytes — full file: out/class-{cname}.txt\n")
        f.write(data)

rc_bad = [(p, t, rc) for p, t, v, rc, d, o, c, w in pkg_rows if rc not in (0, None, "0")]
oc_totals = defaultdict(int)
for o in outcome_by_file.values():
    oc_totals[o] += 1
print(f"files={len(files)} pkg_captures={n_records} py_segments={n_segments} rows={len(pkg_rows)}")
print(f"run outcomes (per source file): {dict(oc_totals)}")
print(f"suppressed (labeled, counted): {dict(suppressed_counts)}")
print(f"nonzero-rc records: {rc_bad}")
for name in buckets:
    total = sum(b.count for b in buckets[name].values())
    print(f"class {name}: {len(buckets[name])} signatures, {total} hits")
print(f"warnings: {len(warn_sigs)} signatures, {sum(b.count for b in warn_sigs.values())} hits")
print(f"output -> {OUT} + {BUNDLES}")
