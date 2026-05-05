#!/usr/bin/env python3
"""Public Content Audit — CI gate for internal-process language in public repo

Scans tracked files for agent attribution names, internal-process vocabulary,
developer-host paths, and credential-like strings that shouldn't appear in
public content. Enforces canonical 10 (secrets handling) in CI.

Exit codes: 0 = clean, 1 = violations found, 2 = script error

Options:
  --dir <path>    Scan specific directory tree (for testing fixtures)
  --file <path>   Scan specific files (repeatable)
  --require-both  When set, must find both block and warn violations (for tests)
  --require-clean When set, expects zero violations (for should-pass tests)
"""

import os
import re
import subprocess
import sys
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST_FILE = Path(__file__).resolve().parent / "check-public-content.allowlist"

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".bmp", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".gz", ".bz2", ".xz", ".zst", ".tar", ".zip", ".7z",
    ".pdf", ".mp3", ".mp4", ".ogg", ".wav",
    ".o", ".a", ".so", ".ko", ".bin", ".exe", ".dll",
    ".pyc", ".pyo", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
}

BINARY_PATHS = {
    "assets/",
    "images/",
}

SKIP_PATHS = {
    ".git/",
    ".github/workflows/public-content-audit.yml",
    "scripts/check-public-content.py",
    "scripts/check-public-content.allowlist",
    "tests/check-public-content/",
    # docs/research/fleet_tooling/ — fleet schema docs intrinsically enumerate
    # the roster (fleet_agents.json shape spec). Path-excepted by design;
    # rosters are load-bearing for the safety-gate plugin's getAllowedPrefixes()
    # lookup in plugins/safety-gate-v2-sketch.ts.
    "docs/research/fleet_tooling/",
}

AGENT_NAMES = [
    ("AGENT-NAME", r"claude-main|claude-laptop|claude-windows|windows-claude|Ubuntu-Claude|InterGenOS-Claude"),
    ("AGENT-NAME", r"chris-ubuntu-code-claude|chris-intergenos-code-claude|chris-windows-code-claude"),
    ("AGENT-NAME", r"chris-ubuntu-codium-deepseek|chris-windows-codium-gemini_flash"),
]

INTERNAL_VOCAB = [
    ("INTERNAL-VOCAB", r"\bPRIME\s+DIRECTIVE\b"),
    ("INTERNAL-VOCAB", r"\bHOLY\s+GRAIL\b"),
    ("INTERNAL-VOCAB", r"\bGLASSWING\b"),
]

OTHER_PROJECTS = [
    ("OTHER-PROJECT", r"\bJARVIS\b"),
    ("OTHER-PROJECT", r"\bVOQR\b"),
    ("OTHER-PROJECT", r"\bemelia_paint\b"),
]

HOME_PATH = [
    ("HOME-PATH", r"/home/christopher/"),
]

INTERNAL_FILES = [
    ("INTERNAL-FILE", r"signing_key_custody.*draft\.md"),
    ("INTERNAL-FILE", r"project_vps_mirror_tracking\.md"),
    ("INTERNAL-FILE", r"\bfeedback_[a-z0-9_]+\.md\b"),
    ("INTERNAL-FILE", r"\bproject_[a-z0-9_]+\.md\b"),
    ("INTERNAL-FILE", r"\breference_[a-z0-9_]+\.md\b"),
    ("INTERNAL-FILE", r"\bcontext_carryover_[a-z0-9_]+\.md\b"),
]

# Agent abbreviations in contextual usage. Standalone "DS" / "WC" can be
# legitimate non-agent acronyms (Direct Sound, water closet, etc.), so we
# anchor on patterns that only the fleet uses: action-verb-prepositions
# ("per SPOC", "by IGOSC", "from DS"); possessive ("DS's directive",
# "SPOC's lane") followed by work-product nouns; or fleet-process phrases
# ("fleet vote", "fleet dispatch", "fleet-wide RFC").
AGENT_ABBREV = [
    ("AGENT-ABBREV", r"\b(?:per|by|from|via|with|told|asked|dispatched)\s+(?:SPOC|IGOSC|WC|DS|GP)\b"),
    ("AGENT-ABBREV", r"\b(?:SPOC|IGOSC|WC|DS|GP)'s\s+(?:lane|directive|dispatch|broadcast|review|note|prior|design|proposal|draft|plan|doc|document|branch|commit|sketch|critique)\b"),
    ("INTERNAL-VOCAB", r"\bfleet[-\s]+(?:vote|review|dispatch|broadcast|protocol|bus|wide|tooling|agents)\b"),
]

HEX_SECRETS = [
    ("HEX-SECRET", r"[0-9a-fA-F]{64}"),
]

WARN_PATTERNS = [
    ("WARN-VOCAB", r"(?i)\bPrime\s+Directive\b"),
    ("WARN-VOCAB", r"(?i)\bHoly\s+Grail\b"),
]

BLOCK_PATTERNS = AGENT_NAMES + AGENT_ABBREV + INTERNAL_VOCAB + OTHER_PROJECTS + HOME_PATH + INTERNAL_FILES + HEX_SECRETS


def load_allowlist(path):
    """Load literal allowlist patterns from file, skipping comments and blanks."""
    patterns = []
    if not path.exists():
        return patterns
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(re.compile(re.escape(line), re.IGNORECASE))
    return patterns


def compile_patterns(specs):
    """Compile (category, regex) tuples into (category, compiled_regex) list."""
    result = []
    for cat, pat in specs:
        try:
            result.append((cat, re.compile(pat)))
        except re.error as e:
            print(f"WARNING: invalid regex '{pat}': {e}", file=sys.stderr)
    return result


def get_tracked_files():
    """Return list of tracked text files from git."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=10
        )
        if result.returncode != 0:
            print("ERROR: git ls-files failed", file=sys.stderr)
            sys.exit(2)
        files = [f for f in result.stdout.split("\0") if f]
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    filtered = []
    for f in files:
        skip = False
        for sp in SKIP_PATHS:
            if f.startswith(sp):
                skip = True
                break
        if skip:
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in BINARY_EXTENSIONS:
            continue
        for bp in BINARY_PATHS:
            if f.startswith(bp):
                skip = True
                break
        if skip:
            continue
        filtered.append(f)
    return filtered


def get_files_in_dir(directory):
    """Return all text files recursively under directory."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        print(f"ERROR: not a directory: {directory}", file=sys.stderr)
        sys.exit(2)
    files = []
    for root, dirs, filenames in os.walk(dir_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in BINARY_EXTENSIONS:
                continue
            full = Path(root) / fn
            files.append(str(full))
    return sorted(files)


def is_sha256_line(line):
    """Check if a line references a SHA-256 or checksum context keyword."""
    return bool(re.search(r"(sha-?256(sum)?|checksum)\b", line, re.IGNORECASE))


def is_allowlisted(line, allowlist_patterns):
    """Check if line matches any allowlist pattern."""
    for pat in allowlist_patterns:
        if pat.search(line):
            return True
    return False


def scan_file(filepath, block_patterns, warn_patterns, allowlist_patterns, repo_root):
    """Scan one file for violations. Returns list of violation tuples."""
    violations = []
    full_path = repo_root / filepath if not os.path.isabs(filepath) else Path(filepath)

    if not full_path.exists():
        return violations

    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, 1):
                line_stripped = line.strip()
                if not line_stripped:
                    continue

                if is_allowlisted(line_stripped, allowlist_patterns):
                    continue

                for cat, pat in block_patterns:
                    match = pat.search(line)
                    if match:
                        matched = match.group(0)
                        if cat == "HEX-SECRET" and is_sha256_line(line):
                            continue
                        display_path = filepath if not os.path.isabs(filepath) else str(Path(filepath).relative_to(repo_root))
                        msg = f"{display_path}:{line_no}: [{cat}] {matched} — remove or replace with public-safe equivalent"
                        violations.append(("block", msg))
                        break

                for cat, pat in warn_patterns:
                    match = pat.search(line)
                    if match:
                        matched = match.group(0)
                        display_path = filepath if not os.path.isabs(filepath) else str(Path(filepath).relative_to(repo_root))
                        msg = f"{display_path}:{line_no}: [{cat}] {matched} — verify this is a legitimate public use"
                        violations.append(("warn", msg))

    except (OSError, UnicodeDecodeError):
        pass

    return violations


def main():
    parser = argparse.ArgumentParser(description="Public content audit scanner")
    parser.add_argument("--dir", help="Scan specific directory tree (for test fixtures)")
    parser.add_argument("--file", action="append", default=[], help="Scan specific file (repeatable)")
    parser.add_argument("--require-clean", action="store_true", help="Exit 1 if any violations found (for should-pass tests)")
    parser.add_argument("--require-both", action="store_true", help="Exit 0 only if both block and warn violations found")
    parser.add_argument("--require-fail", action="store_true", help="Exit 0 only if violations found (for should-fail tests)")

    args = parser.parse_args()

    allowlist_patterns = load_allowlist(ALLOWLIST_FILE)
    block_compiled = compile_patterns(BLOCK_PATTERNS)
    warn_compiled = compile_patterns(WARN_PATTERNS)

    if args.dir:
        files = get_files_in_dir(args.dir)
    elif args.file:
        files = args.file
    else:
        files = get_tracked_files()

    if not files:
        print("ERROR: no files to scan", file=sys.stderr)
        sys.exit(2)

    repo_root = REPO_ROOT if not args.dir else Path(args.dir).resolve()

    all_violations = []
    for filepath in sorted(files):
        violations = scan_file(filepath, block_compiled, warn_compiled, allowlist_patterns, repo_root)
        all_violations.extend(violations)

    blocks = [v for v in all_violations if v[0] == "block"]
    warns = [v for v in all_violations if v[0] == "warn"]

    for sev, msg in all_violations:
        print(msg)

    if blocks:
        print(f"\nBLOCK violations: {len(blocks)}")
    if warns:
        print(f"WARN violations: {len(warns)}")
    if not blocks and not warns:
        print("PASS: no violations found.")

    if args.require_clean:
        sys.exit(0 if not blocks and not warns else 1)
    if args.require_both:
        sys.exit(0 if blocks and warns else 1)
    if args.require_fail:
        sys.exit(0 if all_violations else 1)

    sys.exit(1 if blocks else 0)


if __name__ == "__main__":
    main()
