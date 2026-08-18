#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Public Content Audit — CI gate for internal-process language in public repo

Scans tracked files for agent attribution names, internal-process vocabulary,
developer-host paths, and credential-like strings that shouldn't appear in
public content. Enforces canonical 10 (secrets handling) in CI.

Exit codes: 0 = clean, 1 = violations found, 2 = script error or refused input

A REFUSAL (exit 2) means the scan did not happen — it is deliberately not
exit 1, so no caller can read "the scanner would not run" as "the scanner
found something" or as a clean pass.

Options:
  --dir <path>     Scan specific directory tree (for testing fixtures). This
                   is the sanctioned way to scan a tree outside the
                   repository; --file refuses such paths.
  --file <path>    Scan specific files (repeatable). The path must live
                   inside the repository root — findings are reported as
                   repository-relative paths, so a path outside the root has
                   no honest way to be reported and is refused. It must also
                   name something this scanner can actually read: a path that
                   does not exist, a directory, a dangling symlink, or any
                   non-regular file is refused rather than skipped, because a
                   skipped input is reported as a clean scan.
  --from-ref <ref> Read file content via `git show <ref>:<file>` instead of
                   the working tree, so the audit scans the actually-being-
                   pushed bytes rather than working-tree state. In a pre-push
                   hook pass the LOCAL SHA git supplies for the ref being
                   updated, NOT HEAD: `git push origin <branch>:dev` publishes
                   <branch> while HEAD may be an unrelated checkout, so a
                   HEAD-pinned audit reads content the push is not publishing.
  --require-both   When set, must find both block and warn violations (for tests)
  --require-clean  When set, expects zero violations (for should-pass tests)
"""

import os
import re
import subprocess
import sys
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST_FILE = Path(__file__).resolve().parent / "check-public-content.allowlist"

# Exit code for "the scanner refused to run on this input" — a bad --dir, a
# path outside the repository root, or a --file path that names something
# unreadable. Held apart from the violations exit (1) on purpose: a refusal
# reports nothing about the content, and a caller that read one as a
# detection, or as a clean scan, would be acting on a scan that never ran.
EXIT_REFUSED = 2

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".bmp", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".gz", ".bz2", ".xz", ".zst", ".tar", ".zip", ".7z",
    ".pdf", ".mp3", ".mp4", ".ogg", ".wav",
    ".o", ".a", ".so", ".ko", ".bin", ".exe", ".dll",
    ".pyc", ".pyo", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
}

# BINARY_PATHS is retired (decided 2026-08-16): the former {"assets/",
# "images/"} prefix skip dropped every file under those directories as if
# binary, and 100+ tracked TEXT files (shell-extension JavaScript, theme CSS,
# the backup application, unit files) shipped to installed systems unscanned —
# 7 BLOCK + 1 WARN sat in them while the tree scan reported PASS. Binary
# detection is extension-driven (BINARY_EXTENSIONS) everywhere, including
# under assets/ and images/.

SKIP_PATHS = {
    ".git/",
    ".github/workflows/public-content-audit.yml",
    "scripts/check-public-content.py",
    # The wiki repository's own scanner (scanned when this gate runs in --dir
    # mode over the wiki tree). Scanner-class disposition, decided 2026-08-16:
    # a denylist necessarily contains its own terms, the same reason this
    # file skips itself.
    "scripts/check-web-content.py",
    "scripts/check-public-content.allowlist",
    "tests/check-public-content/",
    # The detector tests for the three 2026-08-05 tiers. Like the fixtures
    # under tests/check-public-content/, this file must CONTAIN the shapes it
    # asserts on — a persona attribution, a private-repository citation and a
    # host shorthand — plus the legitimate neighbours that must NOT match. A
    # detector whose own test cannot hold a sample of what it detects cannot be
    # tested at all.
    "tests/preflight/test_public_language_detectors.py",
    # Same reason, for the 2026-08-16 scope-fix tests: the file must hold
    # samples of the shapes it proves refused.
    "tests/preflight/test_check_public_content_scope.py",
    # docs/research/fleet_tooling/ — fleet schema docs intrinsically enumerate
    # the roster (fleet_agents.json shape spec). Path-excepted by design;
    # rosters are load-bearing for the safety-gate plugin's getAllowedPrefixes()
    # lookup in plugins/safety-gate-v2-sketch.ts.
    "docs/research/fleet_tooling/",
    # intergenos-wiki signed page manifest — a machine-generated JSON of PUBLIC
    # per-page sha256 hashes (nothing secret; the pages themselves are the
    # public wiki), signed (.asc) and verified fail-closed at cite
    # time by intergen.wiki_citations. The 80 hex values trip HEX-SECRET by
    # shape, not by substance. Path-exemption authorized per
    # build-rules §3.11 (planned in the package.yml since authoring; executed
    # at the first-mint manifest ceremony, 2026-07-12).
    "packages/desktop/intergenos-wiki/pages-manifest.json",
}

AGENT_NAMES = [
    ("AGENT-NAME", r"claude-main|claude-laptop|claude-windows|windows-claude|Ubuntu-Claude|InterGenOS-Claude"),
    ("AGENT-NAME", r"chris-ubuntu-code-claude|chris-intergenos-code-claude|chris-windows-code-claude"),
    ("AGENT-NAME", r"chris-ubuntu-codium-deepseek|chris-windows-codium-gemini_flash"),
]

INTERNAL_VOCAB = [
    # The separator is [-\s]+ (hyphen-or-whitespace), NOT \s+: the doctrine
    # phrases leak hyphenated ("Holy-Grail", "Prime-Directive") just as often as
    # spaced, and a whitespace-only pattern reported green while the hyphenated
    # jargon was still shipping — a mask-not-verify hole in the control itself.
    ("INTERNAL-VOCAB", r"\bPRIME[-\s]+DIRECTIVE\b"),
    ("INTERNAL-VOCAB", r"\bHOLY[-\s]+GRAIL\b"),
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

# The private ledger's filename had no category at all (found 2026-08-16 by
# the adversarial release verification: three public files cited it, one of
# them shipped in a unit file). Citations of the private ledger are internal
# references a public reader cannot resolve — same class as INTERNAL-FILE.
# The audit tooling that must LOCATE the ledger to do its work is exempted
# via LEDGER_EXEMPT_PATHS below, mirroring PRIVATE_REPO_PATH_EXEMPT_PATHS.
INTERNAL_LEDGER = [
    ("INTERNAL-LEDGER", r"\bTRACKER(?:_[0-9][0-9._]*)?\.md\b"),
    ("INTERNAL-LEDGER", r"\bTRACKER[ _](?:2\.0|3\.0)\b"),
]

# Machine-specific leak class (decided 2026-07-06, after the
# docs/sessions relocation: 29 session docs full of home-LAN addresses and
# fleet-host names had accumulated in the public tree). These facts are
# private-repo-only. NOTE: the libvirt NAT subnet (192.168.122.x) used by
# the public build-VM docs is deliberately NOT blocked — the leak class is
# the development LAN (192.168.1.x). Bare fleet-host names are
# blocked outright (the phrasal AGENT_ABBREV tier above catches only
# "per SPOC"-style usage; a bare name in public content is still a leak).
MACHINE_SPECIFICS = [
    ("HOME-LAN-IP", r"\b192\.168\.1\.\d{1,3}\b"),
    ("SUDO-PASS-PATH", r"Documents/s\.txt"),
    # Global-unicast IPv6 literals (added 2026-08-18, completing this tier:
    # the RFC1918 pattern above had no IPv6 counterpart). Global unicast is
    # 2000::/3, so the first hextet starts with 2 or 3. Shape guards:
    #   - two colons minimum, so USB vendor:product IDs (20a0:42b2) and
    #     standards citations (SMPTE-2086:2014) never match;
    #   - the leading lookbehind refuses a hex-or-colon predecessor, so the
    #     interior segments of a link-local address (fe80::...) never match;
    #   - carved out in-pattern, as public infrastructure rather than
    #     machine-specific values: the RFC 3849 documentation prefix
    #     (2001:db8::/32) and the Cloudflare (2606:4700::/32) and Quad9
    #     (2620:fe::/32) resolver prefixes used by shipped DNS defaults,
    #     their docs, and test fixtures.
    ("GLOBAL-IPV6",
     r"(?<![0-9A-Fa-f:.])\b(?!2001:0?[Dd][Bb]8\b)(?!2606:4700\b)(?!2620:[Ff][Ee]\b)"
     r"[23][0-9A-Fa-f]{3}:[0-9A-Fa-f]{0,4}:[0-9A-Fa-f:]*[0-9A-Fa-f]\b"),
]
# Bare fleet-host names are BLOCK-tier — decided 2026-07-08: there
# are NO legitimate uses of fleet seat names in public content, period. The
# 104 pre-existing sites this tier used to WARN about were swept to
# role-neutral phrasing the same day the ruling landed; the allowlist is
# deliberately unable to suppress this category (see ALLOWLIST_IMMUNE_CATS
# + scan_file), so neither an allowlist entry nor a path exemption can
# reopen it. The only exempt surfaces are the checker's own machinery
# (SKIP_PATHS: this file, its allowlist, its tests) and the documented
# fleet_tooling roster schema — mechanical necessities, not judgment calls.
# The distinctive full seat names (SPOC, ZIGOSC, IGOSC) are bare-blocked here —
# they are coined tokens with zero false-positive risk. ZIGOSC was missing from
# this alternation (and `\bIGOSC\b` does NOT match inside "ZIGOSC" — no word
# boundary between Z and I), so a bare "ZIGOSC" leaked past BOTH this tier and the
# phrasal AGENT_ABBREV tier undetected until it was caught in review; added here to
# complete the 2026-07-08 ruling, which names ZIGOSC explicitly. The 2-letter seats
# (WC/DS/GP) stay contextual-only (AGENT_ABBREV) by design — bare-blocking them
# would false-positive on ordinary acronyms (water closet, etc.).
# (?i): the tier was compiled case-sensitively until 2026-08-16, so a
# lowercase seat spelling passed the gate — one existed in a build-script
# comment and the gate returned PASS on that file. The coined tokens have no
# legitimate lowercase use either, so the whole tier is case-insensitive.
# The alternation is assembled from pieces so this definition never spells
# the bare tokens (the language gate's self-reference discipline; see the
# devicepath example in check-public-language.py).
FLEET_HOST_BLOCK = [
    ("FLEET-HOST", r"(?i)\b(?:" + "SP" "OC" + "|" + "ZIG" "OSC" + "|" + "IG" "OSC" + r")\b"),
]

# ---------------------------------------------------------------------------
# Language classes this checker could not see until 2026-08-05.
#
# The three tiers below close a measured hole. This scanner passed CLEAN on a
# tree that carried 192 lines of persona attribution, 28 private-repository
# path citations, and 39 development-host shorthands — none of the existing
# tiers described those shapes, so the enforcement for that half of the
# public-language rule was human attention, and human attention does not scale
# to 1,150 recipes. Each tier below was proven against a fixture BEFORE it was
# added to BLOCK_PATTERNS, and the tree was swept before the tier was armed;
# a fail-closed gate armed over a tree still carrying hits blocks every push
# on day one and teaches people to route around it.
# ---------------------------------------------------------------------------

# 1. Attribution of a DECISION to a person or a project role.
#
# The rule is about who a decision is credited to, not about the word
# "operator". Naming a human who PERFORMS A STEP is ordinary technical writing
# and stays legal: "the operator inserts the card", "the operator reads the
# timestamps". What leaves is "the operator ruled/approved/ratified X" — the
# governance voice — which becomes "decided <date>: <rationale>".
#
# Hyphenated compounds are listed before the phrase forms for a reason found
# the hard way while sweeping: a phrase rule that matches "the operator's"
# will happily consume the head of a role-plus-verb compound and leave its
    # verb half
# stranded mid-word.
PERSONA_ATTRIBUTION = [
    ("PERSONA-ATTRIBUTION",
     r"(?i)\b(?:owner|operator|coordinator)-"
     r"(?:ruled|ruling|directed|direct|declared|approved|greenlit|ratified|"
     r"confirmed|mandated|decided|authori[sz]ed|adopted|included|locked|"
     r"paced|ordered|instructed|directive)\b"),
    ("PERSONA-ATTRIBUTION",
     r"(?i)\b(?:the\s+)?(?:owner|operator|coordinator)\s+"
     r"(?:ruled|declared|directed|ratified|mandated|decided|approved)\b"),
    ("PERSONA-ATTRIBUTION",
     r"(?i)\b(?:owner|operator|coordinator)'s\s+"
     r"(?:ruling|decision|directive|direction|word|call|approval|mandate)\b"),
    ("PERSONA-ATTRIBUTION",
     r"(?i)\bper\s+(?:the\s+)?(?:owner|operator|coordinator)\b(?!\s+(?:reads|inserts|enters|types|chooses|selects|confirms\s+the))"),
    ("PERSONA-ATTRIBUTION",
     r"(?i)\b(?:owner|operator)\s+directive\b"),
]

# 2. Citations of the private repository. A reader of the public tree cannot
# follow one, so every citation is also a dangling pointer — and six of them
# used to print inside gate FAILURE output, which is the moment a reader is
# least able to go looking.
#
# EXEMPT, because these files must LOCATE that repository to do their work and
# the path is functional rather than a citation:
#   scripts/anchor-tracker.sh, scripts/audit-package.py,
#   scripts/audit-rule5-sweep.py, scripts/aggregate-package-audits.py — audit
#     tooling that reads the tracker and writes audit output there;
#   .githooks/pre-push — documents the environment variable that points at it;
#   .gitignore — keeps a nested checkout from being committed by accident.
# NOT exempt and NOT swept: intergen/data/destructive-policy-manifest.json,
# whose citation sits in a field covered by a detached OpenPGP signature that
# is verified fail-closed at load against a pinned fingerprint. Editing one
# character there invalidates the signature and fail-closes the
# destructive-policy never-list to its interim floor, so that one is a re-sign
# ceremony item and carries an allowlist entry instead of an edit.
PRIVATE_REPO_PATH = [
    ("PRIVATE-REPO-PATH", r"\bintergenos-private\b"),
]

# Paths exempt from PERSONA-ATTRIBUTION only — every other tier still applies.
# Two reasons, both specific:
#
#   intergen/governance.py, intergen/tests/test_governance.py — "owner-confirmed"
#     here is PRODUCT language, not project-governance voice. It names the
#     person who owns the installed machine, who must confirm an autonomy-tier
#     elevation; the invariant's meaning lives in that word and neutralising it
#     would delete the thing the check is about.
#   intergen/data/destructive-policy-manifest.json — ships with a detached
#     OpenPGP signature verified fail-closed at load against a pinned
#     fingerprint. Editing one character invalidates the signature and
#     fail-closes the destructive-policy never-list to its interim floor, so
#     its one citation is a re-sign ceremony item rather than a text edit.
#
# An entry here without a stated reason is a bug.
PERSONA_ATTRIBUTION_EXEMPT_PATHS = [
    "intergen/governance.py",
    "intergen/tests/test_governance.py",
    "intergen/data/destructive-policy-manifest.json",
]
PERSONA_ATTRIBUTION_CATS = {cat for cat, _ in PERSONA_ATTRIBUTION}

LEDGER_EXEMPT_PATHS = [
    "scripts/anchor-tracker.sh",
    "scripts/audit-package.py",
    "scripts/audit-rule5-sweep.py",
    "scripts/aggregate-package-audits.py",
    ".githooks/pre-push",
]
LEDGER_CATS = {"INTERNAL-LEDGER"}

PRIVATE_REPO_PATH_EXEMPT_PATHS = [
    "scripts/anchor-tracker.sh",
    "scripts/audit-package.py",
    "scripts/audit-rule5-sweep.py",
    "scripts/aggregate-package-audits.py",
    ".githooks/pre-push",
    ".gitignore",
    "intergen/data/destructive-policy-manifest.json",
]
PRIVATE_REPO_PATH_CATS = {cat for cat, _ in PRIVATE_REPO_PATH}

# 3. Development-host shorthand: a bare last octet standing in for a machine,
# as in "observed on .241" or "the .192 box".
#
# This tier is CONTEXTUAL by design, and that is a deliberate accuracy choice
# rather than a weaker rule. A bare `\.\d{1,3}` matches 165 places in this
# tree, and nearly all of them are SVG path coordinates, version fragments and
# sed expressions — a tier that noisy gets exempted into uselessness within a
# week. Anchored to the words that actually surround a machine reference, it
# matched 39 places and every one of them was a real host shorthand. The same
# reasoning already governs the two-letter seat names in AGENT_ABBREV above.
#
# The trailing guard allows a sentence-ending period ("seen on .241.") while
# still refusing a real dotted quad, because the first version of this pattern
# missed exactly that line.
_OCTET = r"(?:1?\d?\d|2[0-4]\d|25[0-5])"
HOST_SHORTHAND = [
    ("HOST-SHORTHAND",
     r"(?i)\b(?:on|at|from|the|to|via|onto|against)\s+\."
     + _OCTET + r"(?![0-9A-Za-z])(?!\.\d)"),
    ("HOST-SHORTHAND",
     r"(?<![0-9A-Za-z.])\." + _OCTET
     + r"\s+(?:box|seat|host|machine|workstation|node|target)\b"),
]

# Path prefixes exempt from MACHINE_SPECIFICS ONLY (all other BLOCK tiers
# still apply). Each entry documents WHY it is exempt — an entry without a
# reason is a bug:
#   - intergen/tests/test_ip_answer.py — synthetic ifconfig fixtures; the
#     tests exercise RFC1918 internal-IP parsing, so the fixture addresses
#     MUST be RFC1918 by test semantics (TEST-NET would change behavior).
#     Also carries synthetic global-unicast IPv6 fixtures (2601:abc::...)
#     for the same parsing tests — exempt for the same reason (2026-08-18).
#   (The docs/research/ai_integration/ entry that stood here from 2026-07-06
#   was removed 2026-08-18: the archived transcripts now use placeholder
#   identifiers and the directory scans like every other path. Entries on
#   this list carry an expiry condition, never an open-ended "pending".)
MACHINE_SPECIFICS_EXEMPT_PATHS = [
    "intergen/tests/test_ip_answer.py",
    #   - intergen/data/howto/networking.json — user-facing howto uses
    #     192.168.1.20 as a generic home-router example (idiomatic for
    #     networking docs; not a fleet address; an edit would flip the
    #     intergen template hash for zero leak reduction).
    "intergen/data/howto/networking.json",
]
MACHINE_SPECIFICS_CATS = {cat for cat, _ in MACHINE_SPECIFICS}

# Session evals/analyses/runbooks are private-repo-only, wholesale
# (decided 2026-07-06) — any tracked file under this prefix is a
# violation regardless of content. Scrubbed public derivatives get authored
# under a different path deliberately, never dropped here.
SESSIONS_PRIVATE_PREFIX = "docs/sessions/"

WARN_PATTERNS = [
    # [-\s]+ separator (see INTERNAL_VOCAB) so the hyphenated spelling cannot
    # slip past the warn tier the way it slipped past the block tier.
    ("WARN-VOCAB", r"(?i)\bPrime[-\s]+Directive\b"),
    ("WARN-VOCAB", r"(?i)\bHoly[-\s]+Grail\b"),
]

# Path prefixes where WARN-VOCAB is exempted. The WARN-VOCAB rule catches
# casual leak of internal-strategy jargon ("Prime Directive", "Holy Grail")
# into surfaces where it would read as project-internal noise. The exempt
# paths below are the surfaces where the vocabulary IS the appropriate
# discipline anchor and rephrasing would lose meaning:
#
#   - docs/research/audit/reviews/ — peer-review dispatches prescribe the
#     terms structurally
#   - docs/audit/ — audit docs use Holy Grail as a severity-classification
#     category (Holy-Grail tier = security-only-alignment violation)
#   - docs/research/ — research docs cite the doctrine as motivation
#   - docs/operational/ — operational runbooks cite the doctrine to scope
#     security-posture impact
#   - docs/mirror/ — mirror design doc has a dedicated Holy Grail
#     considerations section
#   - development-status/ — internal dev tracker uses Holy Grail as a
#     categorization tag for security-aligned items
#   - EXPORT-NOTICE.md / TRADEMARK.md — public-facing legal docs that name
#     the doctrine explicitly to scope export-control + trademark posture
#     (the name IS the canonical project-public reference per README.md)
#   - installer/backend/disks.py + scripts/chroot-config-ch9.sh +
#     scripts/reload-slot-9c.sh — code-discipline comments documenting
#     non-obvious security reasoning per CLAUDE.md "comment when WHY is
#     non-obvious" rule; rephrasing would lose the discipline-anchor
#
# BLOCK_PATTERNS still apply — this exempts WARN-only.
WARN_EXEMPT_PATHS = [
    "docs/research/audit/reviews/",
    "docs/audit/",
    "docs/research/",
    "docs/operational/",
    "docs/mirror/",
    "development-status/",
    "EXPORT-NOTICE.md",
    "TRADEMARK.md",
    "installer/backend/disks.py",
    "scripts/chroot-config-ch9.sh",
    "scripts/reload-slot-9c.sh",
]

BLOCK_PATTERNS = (
    AGENT_NAMES + AGENT_ABBREV + INTERNAL_VOCAB + OTHER_PROJECTS + HOME_PATH
    + INTERNAL_FILES + HEX_SECRETS + INTERNAL_LEDGER + MACHINE_SPECIFICS + FLEET_HOST_BLOCK
    # Armed LAST, after the tree was swept: 192 persona-attribution lines, 6
    # sweepable private-repository citations and 39 host shorthands were
    # removed first, so arming these three tiers does not block a push against
    # a tree that still carries hits.
    + PERSONA_ATTRIBUTION + PRIVATE_REPO_PATH + HOST_SHORTHAND
)

# Categories the allowlist can NEVER suppress (decided 2026-07-08:
# no legitimate uses exist, so no allowlist entry may create one). Applied
# per-match in scan_file — the allowlist keeps working for every other tier.
ALLOWLIST_IMMUNE_CATS = {"FLEET-HOST"}


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


def refuse(message):
    """Print a named refusal and exit — never a traceback, never exit 1.

    The message names the offending input and says why it is refused, so the
    reader does not have to infer the cause from a stack trace.
    """
    print(f"REFUSED: {message}", file=sys.stderr)
    sys.exit(EXIT_REFUSED)


def repo_relative_path(filepath, repo_root):
    """Return the repository-relative form of a requested path, or refuse.

    Every finding this scanner prints is anchored to a repository-relative
    path, so a requested path that does not live under repo_root has no
    honest way to be reported.

    Before this check, the two shapes of an out-of-repo --file argument both
    failed and both failed badly. An out-of-repo path that EXISTED reached
    Path.relative_to and died with an unhandled ValueError traceback whose
    exit status was 1 — the same status this scanner uses for "violations
    found", so at a glance a crash was indistinguishable from a detection.
    An out-of-repo path that did NOT exist was read as None, skipped, and
    reported as "PASS: no violations found" with exit 0 — a scan of nothing
    presented as a clean result. Both now take the same named refusal.

    Containment is decided LEXICALLY — the path normalised against repo_root,
    with symlinks left unresolved. Resolving them would refuse a tracked
    symlink whose target lives outside the tree, and such a file is still a
    file of this repository that the audit is supposed to read.
    """
    root = os.path.normpath(str(repo_root))
    if os.path.isabs(filepath):
        candidate = os.path.normpath(filepath)
    else:
        candidate = os.path.normpath(os.path.join(root, filepath))

    relative = os.path.relpath(candidate, root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        refuse(
            f"{filepath}: outside the repository root {root} — this audit "
            f"reports findings as repository-relative paths and will not "
            f"scan a path that has none. Pass a path inside the repository, "
            f"or use --dir to scan a tree outside it."
        )
    return relative


def require_readable_file(filepath, display_path, repo_root, from_ref=None):
    """Refuse a --file argument this scanner cannot honestly read.

    Only caller-named --file paths reach here. A path from --dir comes out of
    os.walk and a path from the git file lists comes out of the index or a
    tree object, so neither is a place a caller can point at nothing; --file
    is.

    Every shape below used to be SKIPPED rather than refused, and a skipped
    input is indistinguishable from a scanned-and-clean one. read_file_content
    turns a missing path into None (the `not full_path.exists()` early return)
    and turns every OSError into None as well, so `--file` on a path that did
    not exist, and `--file` on a DIRECTORY (open() raises IsADirectoryError,
    an OSError), both printed "PASS: no violations found." and exited 0. A
    scan of nothing was reported as a clean result.

    A non-regular file is refused for a sharper reason, measured rather than
    reasoned: `--file` naming a NAMED PIPE did not report anything at all —
    open() blocked on the fifo waiting for a writer and the process never
    returned. A gate that hangs forever is worse than one that answers wrong,
    and no caller of this scanner has a timeout around it.

    With --from-ref the content comes from `git show <ref>:<path>`, not from
    the working tree, so existence is decided against THAT ref. Deciding it
    against the working tree instead would refuse a file that legitimately
    exists only in the ref being pushed, and would let the refusal be turned
    off by adding a flag.
    """
    if from_ref:
        target = f"{from_ref}:{display_path}"
        try:
            result = subprocess.run(
                ["git", "cat-file", "-t", target],
                capture_output=True, text=True, cwd=repo_root, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            refuse(
                f"{filepath}: could not be looked up in {from_ref} "
                f"(git cat-file -t {target} failed: {e}) — refusing to "
                f"report a scan of input this audit never read."
            )
        if result.returncode != 0:
            refuse(
                f"{filepath}: does not exist in {from_ref} — this audit reads "
                f"--file content from that ref and will not report a path it "
                f"could not read as clean."
            )
        object_type = result.stdout.strip()
        if object_type != "blob":
            refuse(
                f"{filepath}: is a git {object_type} in {from_ref}, not a "
                f"file. Pass a file, or use --dir to scan a tree."
            )
        return

    full_path = Path(filepath) if os.path.isabs(filepath) else Path(repo_root) / filepath

    if not os.path.lexists(full_path):
        refuse(
            f"{filepath}: does not exist — this audit will not report a path "
            f"it could not read as a clean scan. Check the path, or use --dir "
            f"to scan a tree."
        )
    if not os.path.exists(full_path):
        refuse(
            f"{filepath}: is a symbolic link whose target does not exist — "
            f"there is nothing to read, and an unread file must not be "
            f"reported as clean."
        )
    if os.path.isdir(full_path):
        refuse(
            f"{filepath}: is a directory — --file scans one file. Use --dir "
            f"to scan a tree, so every file under it is actually read."
        )
    if not os.path.isfile(full_path):
        refuse(
            f"{filepath}: is not a regular file — this audit reads file "
            f"content, and opening a special file either blocks forever or "
            f"yields nothing that can honestly be reported."
        )


def get_tracked_files(from_ref=None):
    """Return list of tracked text files from git.

    When from_ref is set, returns files in that ref via `git ls-tree -r <ref>`
    instead of the working tree's index. Pre-push gates pass HEAD here so the
    file list matches the bytes the audit will scan.
    """
    try:
        if from_ref:
            cmd = ["git", "ls-tree", "-r", "-z", "--name-only", from_ref]
        else:
            cmd = ["git", "ls-files", "-z"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=10
        )
        if result.returncode != 0:
            print(f"ERROR: {' '.join(cmd)} failed", file=sys.stderr)
            sys.exit(2)
        files = [f for f in result.stdout.split("\0") if f]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        # Narrowed from broad except per §7 GP-10 audit — git/IO errors only.
        # A logic flaw in this function would now propagate uncaught, which
        # is what we want for a gate script (fail loudly, not silently).
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
        filtered.append(f)
    return filtered


def get_files_in_dir(directory):
    """Return all text files recursively under directory, as absolute paths.

    The walk starts from the RESOLVED directory, which is what makes the
    returned paths usable by the rest of the scan. main() sets the scan root
    for --dir to Path(args.dir).resolve(), an absolute path, and
    read_file_content joins a relative path onto that root. Walking the
    caller's spelling therefore produced paths that were already relative to
    the root and got joined onto it a second time, so every file resolved to
    a doubled path that does not exist, read as None, and was skipped.

    The effect was a whole scan reporting a clean result having read nothing.
    Measured on a fixture tree whose single file carried three BLOCK
    violations: --dir with an ABSOLUTE path reported all three and exited 1,
    while --dir with a RELATIVE path to the same tree printed "PASS: no
    violations found." and exited 0. Absolute spellings were unaffected,
    which is why this survived: every invocation that mattered used one.

    Resolving here matches what main() already does to build the root, so the
    two agree by construction rather than by the caller's choice of spelling.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        # Same refusal class as an out-of-repo --file: the scan cannot run on
        # this input, so it exits EXIT_REFUSED rather than the violations code.
        # The message names the path AS THE CALLER TYPED IT, not the resolved
        # form, so it is recognisable to whoever passed it.
        print(f"ERROR: not a directory: {directory}", file=sys.stderr)
        sys.exit(EXIT_REFUSED)
    dir_path = dir_path.resolve()
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
            # SKIP_PATHS applies in --dir mode too (fixed 2026-08-16: the
            # tracked-files path consulted it, this walk did not, so a --dir
            # scan of another repository flagged that repository's own
            # scanner — the scanner-class skip below only ever worked for
            # tracked-file scans of THIS repository).
            rel = str(full.relative_to(dir_path))
            if any(rel == sp or rel.startswith(sp) for sp in SKIP_PATHS):
                continue
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


def read_file_content(filepath, repo_root, from_ref=None):
    """Read file content from working tree or from a git ref.

    When from_ref is set, content is read via `git show <ref>:<filepath>`
    so the audit operates on the pushed-bytes, not working-tree-bytes.
    Returns text content (decoded UTF-8 with error-replace), or None if
    the file is binary / unreadable / missing.
    """
    if from_ref:
        try:
            result = subprocess.run(
                ["git", "show", f"{from_ref}:{filepath}"],
                capture_output=True, cwd=repo_root, timeout=10
            )
            if result.returncode != 0:
                return None
            return result.stdout.decode("utf-8", errors="replace")
        except Exception:
            return None
    else:
        full_path = repo_root / filepath if not os.path.isabs(filepath) else Path(filepath)
        if not full_path.exists():
            return None
        try:
            with open(full_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None


def scan_file(filepath, block_patterns, warn_patterns, allowlist_patterns, repo_root, from_ref=None):
    """Scan one file for violations. Returns list of violation tuples.

    When from_ref is set, file content is read via git-show against that ref
    so the audit scans the actually-pushed bytes (relevant for pre-push gates
    where working-tree may differ from HEAD).
    """
    violations = []

    # Decide the reported path BEFORE reading anything: an out-of-repo path is
    # refused whether or not the file exists, so a missing one cannot slip
    # through the content-is-None early return and be counted as clean.
    display_path = repo_relative_path(filepath, repo_root)

    # `git show <ref>:<path>` resolves its path against the repository root,
    # so it must be handed the repository-relative form. Handing it the
    # caller's spelling made an ABSOLUTE --file path silently miss: git show
    # failed, read_file_content turned that into None, and the file was
    # reported clean. Measured at 1e8d19b0 on the tree's own agent-name
    # fixture — the relative spelling reported 3 BLOCK violations and exit 1
    # while the absolute spelling of the same file printed "PASS: no
    # violations found." at exit 0. The working-tree read takes the caller's
    # spelling unchanged; it resolves an absolute path correctly on its own.
    read_path = display_path if from_ref else filepath

    content = read_file_content(read_path, repo_root, from_ref=from_ref)
    if content is None:
        return violations

    # Session docs are private-repo-only, wholesale — flag the file itself,
    # not its lines (content is irrelevant; the path is the violation).
    if display_path.startswith(SESSIONS_PRIVATE_PREFIX):
        violations.append(("block", (
            f"{display_path}: [SESSIONS-PRIVATE] session docs are "
            f"private-repo-only (decided 2026-07-06) — author "
            f"scrubbed public derivatives under a different path"
        )))
        return violations

    machine_specifics_exempt = any(
        display_path.startswith(p) for p in MACHINE_SPECIFICS_EXEMPT_PATHS)

    # The files that must LOCATE the private repository to do their work, plus
    # the one signed artifact whose citation cannot be edited without a re-sign
    # ceremony. Every other tier still applies to them.
    private_repo_path_exempt = any(
        display_path == p or display_path.startswith(p.rstrip("/") + "/")
        for p in PRIVATE_REPO_PATH_EXEMPT_PATHS)

    persona_attribution_exempt = any(
        display_path == p or display_path.startswith(p.rstrip("/") + "/")
        for p in PERSONA_ATTRIBUTION_EXEMPT_PATHS)

    ledger_exempt = any(
        display_path == p or display_path.startswith(p.rstrip("/") + "/")
        for p in LEDGER_EXEMPT_PATHS)

    # Per WARN_EXEMPT_PATHS: audit-review files reference WARN-VOCAB terms
    # structurally (dispatch format prescribes them). Skip warn scan for those.
    effective_warn_patterns = warn_patterns
    if any(display_path.startswith(p) for p in WARN_EXEMPT_PATHS):
        effective_warn_patterns = []

    for line_no, line in enumerate(content.splitlines(), 1):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Allowlist suppression is applied PER MATCH, not per line: categories
        # in ALLOWLIST_IMMUNE_CATS must block even on an allowlisted line —
        # otherwise a broad allowlist literal would silently reopen a ruled-
        # closed class (the fleet-name ruling, 2026-07-08).
        line_allowlisted = is_allowlisted(line_stripped, allowlist_patterns)

        for cat, pat in block_patterns:
            match = pat.search(line)
            if match:
                if line_allowlisted and cat not in ALLOWLIST_IMMUNE_CATS:
                    continue
                matched = match.group(0)
                if cat == "HEX-SECRET" and is_sha256_line(line):
                    continue
                if cat in MACHINE_SPECIFICS_CATS and machine_specifics_exempt:
                    continue
                if cat in PRIVATE_REPO_PATH_CATS and private_repo_path_exempt:
                    continue
                if cat in PERSONA_ATTRIBUTION_CATS and persona_attribution_exempt:
                    continue
                if cat in LEDGER_CATS and ledger_exempt:
                    continue
                msg = f"{display_path}:{line_no}: [{cat}] {matched} — remove or replace with public-safe equivalent"
                violations.append(("block", msg))
                break

        if line_allowlisted:
            continue

        for cat, pat in effective_warn_patterns:
            match = pat.search(line)
            if match:
                matched = match.group(0)
                msg = f"{display_path}:{line_no}: [{cat}] {matched} — verify this is a legitimate public use"
                violations.append(("warn", msg))

    # WRAP SCAN (decided 2026-08-16): a token split across a comment-line
    # break defeats the per-line scan — a tracked internal filename wrapped
    # over two comment lines passed the gate while the token sat whole in the
    # file. Each adjacent line PAIR is re-scanned with the break and the
    # second line's comment leader removed, for the token-shaped tiers only
    # (filenames and coined seat names; the phrasal tiers stay per-line by
    # design). A pair hit already reported by the line scan is not repeated.
    token_cats = {"INTERNAL-FILE", "FLEET-HOST"} | LEDGER_CATS
    token_patterns = [(c, q) for c, q in block_patterns if c in token_cats]
    if token_patterns:
        lines = content.splitlines()
        already = {v[1].split(" — ")[0].rsplit(": ", 1)[-1] for v in violations}
        for i in range(len(lines) - 1):
            second = re.sub(r"^[\s#/*>-]+", "", lines[i + 1])
            joined = lines[i].rstrip() + second
            for cat, pat in token_patterns:
                match = pat.search(joined)
                if not match:
                    continue
                matched = match.group(0)
                if f"[{cat}] {matched}" in already:
                    continue
                if pat.search(lines[i]) or pat.search(lines[i + 1]):
                    continue  # whole on one line: the line scan owns it
                if cat in LEDGER_CATS and ledger_exempt:
                    continue
                already.add(f"[{cat}] {matched}")
                msg = (f"{display_path}:{i + 1}: [{cat}] {matched} — token wrapped "
                       f"across lines {i + 1}-{i + 2}; remove or replace with "
                       f"public-safe equivalent")
                violations.append(("block", msg))

    return violations


def scan_commit_messages(range_spec, block_patterns, warn_patterns, allowlist_patterns):
    """Scan commit messages in a git range for the same patterns we use on file content.

    range_spec is a git rev-list argument like 'origin/master..HEAD' or 'a..b'.
    Returns list of violation tuples like scan_file does.
    """
    violations = []
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H", range_spec],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=10, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        # Fail CLOSED: an audit that could not enumerate its commits has
        # checked nothing, and returning an empty list here made the caller
        # print PASS on unread input (the pre-push hook treats exit 0 as
        # pass). The failure is reported as a blocking violation so the run
        # exits non-zero through the normal reporting path.
        print(f"ERROR: git log {range_spec} failed: {e}", file=sys.stderr)
        violations.append((
            "block",
            f"commit-message audit could not run — git log {range_spec} "
            f"failed ({e}); refusing to report PASS on unread input",
        ))
        return violations

    shas = [s for s in result.stdout.splitlines() if s.strip()]
    for sha in shas:
        try:
            msg_result = subprocess.run(
                ["git", "log", "-1", "--format=%B", sha],
                capture_output=True, text=True, cwd=REPO_ROOT, timeout=10, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            # Same class, one commit wide: a message that could not be read
            # is an unchecked commit, not a checked-clean one.
            violations.append((
                "block",
                f"commit {sha[:8]}: message could not be read ({e}) — "
                f"unchecked, refusing to report it clean",
            ))
            continue

        msg_text = msg_result.stdout
        for line_no, line in enumerate(msg_text.splitlines(), 1):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            # Same per-match allowlist rule as scan_file: ALLOWLIST_IMMUNE_CATS
            # block even on an allowlisted line (fleet-name ruling 2026-07-08).
            line_allowlisted = is_allowlisted(line_stripped, allowlist_patterns)

            for cat, pat in block_patterns:
                match = pat.search(line)
                if match:
                    if line_allowlisted and cat not in ALLOWLIST_IMMUNE_CATS:
                        continue
                    matched = match.group(0)
                    if cat == "HEX-SECRET" and is_sha256_line(line):
                        continue
                    msg = f"commit {sha[:8]} msg:{line_no}: [{cat}] {matched} — remove or replace with public-safe equivalent (amend before push)"
                    violations.append(("block", msg))
                    break

            if line_allowlisted:
                continue

            # Commit messages use WARN_PATTERNS as-is (no path-based exemption;
            # commit messages are unconditionally part of public history).
            for cat, pat in warn_patterns:
                match = pat.search(line)
                if match:
                    matched = match.group(0)
                    msg = f"commit {sha[:8]} msg:{line_no}: [{cat}] {matched} — verify legitimate public use in commit message"
                    violations.append(("warn", msg))

    return violations


def main():
    parser = argparse.ArgumentParser(description="Public content audit scanner")
    parser.add_argument("--dir", help="Scan specific directory tree (for test fixtures)")
    parser.add_argument("--file", action="append", default=[], help="Scan specific file (repeatable)")
    parser.add_argument("--from-ref", help="Read file content via `git show <ref>:<file>` (pre-push: --from-ref HEAD)")
    parser.add_argument("--commit-msgs", help="Scan commit messages in given range (e.g. 'origin/master..HEAD' for pre-push)")
    parser.add_argument("--require-clean", action="store_true", help="Exit 1 if any violations found (for should-pass tests)")
    parser.add_argument("--require-both", action="store_true", help="Exit 0 only if both block and warn violations found")
    parser.add_argument("--require-fail", action="store_true", help="Exit 0 only if violations found (for should-fail tests)")

    args = parser.parse_args()

    allowlist_patterns = load_allowlist(ALLOWLIST_FILE)
    block_compiled = compile_patterns(BLOCK_PATTERNS)
    warn_compiled = compile_patterns(WARN_PATTERNS)

    all_violations = []

    if args.commit_msgs:
        # Commit-message scan mode (per Group P): scan messages in a range
        # for the same patterns we use on file content. Useful as a pre-push
        # gate to catch agent abbrevs / internal vocab in commit messages
        # before they become public history.
        all_violations.extend(
            scan_commit_messages(args.commit_msgs, block_compiled, warn_compiled, allowlist_patterns)
        )
        # When --commit-msgs is the only mode, skip file scan.
        if not args.dir and not args.file and not args.from_ref:
            files = []
        else:
            if args.dir:
                files = get_files_in_dir(args.dir)
            elif args.file:
                files = args.file
            else:
                files = get_tracked_files(from_ref=args.from_ref)
    else:
        if args.dir:
            files = get_files_in_dir(args.dir)
        elif args.file:
            files = args.file
        else:
            files = get_tracked_files(from_ref=args.from_ref)

    if not files and not args.commit_msgs:
        print("ERROR: no files to scan", file=sys.stderr)
        sys.exit(2)

    repo_root = REPO_ROOT if not args.dir else Path(args.dir).resolve()

    # Refuse every unscannable path up front, before any file is opened, so a
    # run that will be refused does not first emit findings for the files it
    # did manage to scan. Paths from --dir and from the git file lists are
    # inside their root by construction; --file is the surface a caller can
    # point anywhere.
    #
    # Containment is checked for every source. Readability is checked only for
    # caller-named --file paths: --dir paths come from os.walk and git-list
    # paths come from the index or a tree object, so a caller cannot aim those
    # at something that is not there.
    scanning_named_files = bool(args.file) and not args.dir
    for filepath in files:
        display_path = repo_relative_path(filepath, repo_root)
        if scanning_named_files:
            require_readable_file(filepath, display_path, repo_root,
                                  from_ref=args.from_ref)

    for filepath in sorted(files):
        violations = scan_file(filepath, block_compiled, warn_compiled, allowlist_patterns, repo_root, from_ref=args.from_ref)
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
