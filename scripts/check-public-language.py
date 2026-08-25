#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Public-repo language gate — fail-closed enforcement of the public-artifact
language boundary (rulebook Rule 22 / build-rules Rule I).

Neutral-language enforcement for the public tree. The list of banned terms is
NOT stored here or anywhere in the public tree: it is held privately, outside
the repo, and loaded at run time. This module references the banned CLASSES
abstractly (internal identifiers, development-model names, host identifiers) and
carries ZERO literal terms of its own — so the gate itself passes the gate.

Design (why each choice):
  * PRIVATE, EXTERNAL term list. Embedding the terms in a committed script is the
    very recontamination this gate exists to stop. The list lives at a private
    path under the runner's config dir; it is never committed.
  * FAIL-CLOSED BOTH WAYS. A term hit blocks the push with the offending line
    quoted. A missing / unreadable / EMPTY term list ALSO blocks, with a loud
    error naming the exact path — absence must never be a silent bypass.
  * RANGE-SCOPED. Only the ADDED lines of the push range and the range's commit
    messages are scanned — the bytes this push introduces — not the whole tree
    (pre-existing content is a separate remediation, not a per-push gate's job).
  * JOINED ACROSS A WRAP. The added lines of one file are scanned in runs of
    CONSECUTIVE lines joined by a single space, and each commit message is
    scanned as one run, so a term whose spelling is two words is found when the
    author's editor wrapped the line between them. Until 2026-08-25 the gate
    matched each added line alone and every such term passed a wrap. The report
    names the line the match STARTS on; single-line hits report as before. Only
    consecutive lines join, so no adjacency the file does not have is invented.
  * WORD-BOUNDARY, SPAN-AWARE. A bare short token matched as a standalone word is
    a hit (this is the evasion class the contextual-only predecessor missed). A
    legitimate technical collision is exempted at ITS SPAN, so a real hit on the
    same line still blocks. Exemptions are in code and public-safe by
    construction (they describe AUTHORIZED usage, never a banned token):
      - Co-Authored-By trailers pass whole-line (Rule 22 authorized home:
        deliberate AI-co-authorship disclosure, never stripped);
      - InterGen product model-stack references pass (Rule 22 authorized home:
        accurate product provenance);
      - a documented, narrow technical-collision list (extend here, never in the
        private term list).

Exit codes: 0 = clean, 1 = blocked (a hit, or a fail-closed condition).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# The join lives in one module so this gate and the writing-register gate can
# never disagree about what "the same run of added lines" means. sys.path[0] is
# this script's own directory when it runs as a script; the insert keeps the
# import working when a test loads this file by path instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from joined_lines import JoinedText, consecutive_runs  # noqa: E402

# Default private location of the term list (override with the env var). Under
# the runner's XDG-style config dir; NEVER inside any repo.
ENV_VAR = "IGOS_PUBLIC_LANGUAGE_DENYLIST"
DEFAULT_LIST_PATH = Path.home() / ".config" / "intergenos" / "public-language-denylist"


# ---- Exemptions (public-safe by construction — no banned token appears here) --

# A Co-Authored-By trailer stands on every repo (Rule 22 authorized home). The
# whole line is an authorized disclosure surface, so any match on it is exempt.
_TRAILER_RE = re.compile(r"^\s*Co-Authored-By:\s", re.IGNORECASE)

# Span exemptions: if a term match falls INSIDE one of these spans it is not a
# violation. Each pattern is a legitimate, authorized use; adding one here is the
# ONLY way to carve a collision — never by editing the private term list.
_EXEMPT_SPAN_PATTERNS = [
    # InterGen product model-stack references (Rule 22 authorized home): the
    # models that drive InterGen are accurate product provenance, authorized in
    # public. These names are authorized public tokens, not banned terms.
    ("intergen-model-stack", re.compile(r"\b(?:Qwen|InternVL)[\w.\-]*", re.IGNORECASE)),
    # Documented technical collisions of short tokens go here, each with a
    # one-line why (a legitimate non-identifier use whose letters coincide with a
    # short internal token). Entries are added on evidence, in this public file,
    # never in the private list.
    # The extra-tier proprietary download-helper package for Anthropic's CLI
    # coding tool is NAMED after the upstream product (like vscode/chrome);
    # public references to that package/product name are required upstream
    # attribution, not an internal identifier. Evidence: the pkm r11 release
    # note re-scanned on an adjacent edit and hit on the package name.
    ("upstream-product-package-names", re.compile(r"\b(?:claude-code)\b", re.IGNORECASE)),
    # The GUID Partition Table token inside an EFI device-path HD() node
    # (`HD(1,GPT,<guid>,...)` — the UEFI-spec spelling emitted by efibootmgr
    # and quoted verbatim in installer code/tests/commit text): a standards
    # term whose letters coincide with a vendor model-name token. Narrow by
    # construction — only the exact in-node position is exempt, so prose
    # uses of the letters still hit. The pattern is assembled from pieces so
    # this definition never spells the bare token outside its own span
    # (same self-reference discipline as the comment above). Evidence: the
    # 2026-07-16 foreign-OS-detection fix (captured efibootmgr fixtures)
    # blocked on 7 in-devicepath occurrences.
    ("uefi-devicepath-partition-table",
     re.compile(r"HD\(\d+," + "G" "P" "T" + r",")),
    # The POSIX word-count utility in command position (`wc -l`, `| wc -c`):
    # a standard shell tool whose two letters coincide with a short internal
    # token. Narrow by construction — the flag is required, so prose uses of
    # the letters still hit. Evidence: a new tier driver's line-count summary
    # (`ls ... | wc -l`) blocked the 2026-07-15 push.
    ("posix-word-count-command", re.compile(r"\bwc\s+-[lcwmL]\b")),
    # The Xiph audio codec (RFC 6716), whose four-letter name coincides with a
    # vendor model-name token. It ships as its own package, has a 32-bit twin,
    # and is a declared dependency of the audio, media and remote-display
    # stacks.
    #
    # ONE ENTRY, AND WHY IT REPLACED FOUR. This collision used to be carved by
    # four separate position-anchored patterns — dependency-list entry, package
    # metadata, container-format name, 32-bit twin and path element — added one
    # at a time as each new shape blocked a push. That approach failed twice
    # over:
    #
    #   * it never converged. Measured against the tracked tree, five further
    #     shapes were still uncarved and would block the next edit that touched
    #     them: a versioned section heading (`### <name> (1.6.1)`), a recipe
    #     key-value (`name:`/`igos_name:`/`lib32_source:`/`pkg_config:`), a
    #     table cell, a build-tier or audit listing element, and prose naming
    #     the codec beside its sibling codecs. A sixth would have arrived with
    #     the next document.
    #   * one of the four had stopped matching its own comment. The metadata
    #     entry described "a section heading with a version, a container-format
    #     name, a table cell, or a build-tier line" while its pattern accepted
    #     the token after any of start-of-line, space, bracket, comma or `>` —
    #     which is ordinary prose. It was already carving the prose shapes its
    #     comment said it did not, so the narrowness the four entries claimed
    #     was not real, only unstated.
    #
    # So the rule is stated plainly instead: THE LOWERCASE SPELLING IS EXEMPT
    # AS A STANDALONE WORD, ANYWHERE. In this tree it is the package name, and
    # every one of its tracked occurrences is the codec. THE CAPITALISED
    # SPELLING IS NOT — it is exempt only immediately beside codec-domain
    # vocabulary (a container form, or one of codec/audio/container/encoder/
    # decoder), which is how the notices file and the package descriptions
    # write it. That asymmetry is what keeps model attribution blocked, because
    # an attribution is written capitalised; the one authorized place the
    # capitalised form appears on its own is a Co-Authored-By trailer, and the
    # whole trailer line is already exempt above.
    #
    # NAMED RESIDUE: a model attribution written in lowercase, in prose, would
    # pass this carve. Nothing in the tracked tree does that, and the trailer —
    # the form this project actually uses — is covered by its own rule.
    #
    # The pattern is assembled from pieces so this definition never spells the
    # bare token (same self-reference discipline as the entries above).
    ("xiph-audio-codec",
     re.compile(r"(?<![A-Za-z])o" + "pus" + r"(?![A-Za-z])"
                r"|\bO" + "pus" + r"(?:-in-\w+"
                r"|\s+(?:codec|audio|container|encoder|decoder))")),
    # A printf-style integer conversion immediately followed by the letter
    # "s" (a duration in a format string, e.g. logging "after %d" + "s"):
    # the two letters after the % coincide with a short internal token,
    # and the % makes them a standalone word to the boundary matcher.
    # Narrow by construction — only the exact %-conversion position is
    # exempt; prose uses of the letters still hit. The pattern is
    # assembled from pieces so this definition never spells the bare
    # token (same self-reference discipline as the entries above).
    # Evidence: two review-dialog log lines blocked the 2026-07-19 push
    # (reworded that day; this entry carves the collision for the next).
    ("printf-conversion-duration",
     re.compile(r"%[-0-9.*]*l?" + "d" "s" + r"\b")),
    # A regular-expression digit or non-digit character class immediately
    # followed by the letter "s" (e.g. a duration matcher written as a
    # backslash escape + class letter + "s" inside a pattern string): the
    # backslash makes the two letters a standalone word to the boundary
    # matcher, exactly like the %-conversion case above. Narrow by
    # construction — only the exact backslash-escape position is exempt;
    # prose uses of the letters still hit. The pattern is assembled from
    # pieces so this definition never spells the bare token (same
    # self-reference discipline as the entries above). Evidence: surfaced
    # during the 2026-08-03 validate-gate review; reproduced 2026-08-04 on a
    # two-line fixture where the escape form and a prose use both blocked.
    ("regex-digit-class-duration",
     re.compile(r"\\[dD]" + "s" + r"\b")),
    # The documented opt-in cloud scanner providers of the assistant's
    # pluggable security-scanner architecture: a public product feature named
    # in the README, the vision doc, the FAQ, the privacy notice and the
    # component doc. Each is written as a vendor pair — product then owning
    # company, hyphenated or parenthesised — which is the product's own
    # spelling and is distinctive. Narrow by construction: only the paired
    # form is exempt, so a bare vendor model name in prose still hits. The
    # pattern is assembled from pieces so this definition never spells a
    # bare token (same self-reference discipline as the entries above).
    # Evidence: the 2026-07-26 documentation audit measured 145 lines across
    # 55 tracked files that would block on edit, the provider enumeration
    # being the largest class.
    ("assistant-cloud-scanner-providers",
     re.compile(r"(?:" + "Cla" "ude" + r"|" + "Gem" "ini" + r"|CoPilot|Chat" "G" "P" "T"
                r"|Grok)[-\s]?\(?(?:Anthropic|Google|Microsoft|OpenAI|xAI)\)?",
                re.IGNORECASE)),
    # The sixth provider in that same enumeration has no owning-company
    # suffix, so the paired pattern above cannot reach it. Exempt it only in
    # list position — immediately after a separator or coordinating
    # conjunction — which is the only way it appears in the product docs.
    # Prose uses outside a list still hit.
    ("assistant-cloud-scanner-providers-list-tail",
     re.compile(r"(?:[,;]|\bor\b|\band\b)\s*(?:and\s+|or\s+)?" + "Deep" "Seek" + r"\b",
                re.IGNORECASE)),
    # The upstream coding-tool product name in PROSE and in the licence
    # register, beside the existing hyphenated package-name carve above. The
    # register spells the product with a space ("<product> Code (CLI tool …)")
    # and carries a LicenseRef identifier naming the vendor, neither of which
    # the package-name pattern reaches. Required upstream attribution in a
    # legal document, not an internal identifier.
    # Evidence: the 2026-07-26 audit — 13 lines across the licence register,
    # the licence policy, the sources file and the third-party notices.
    ("upstream-product-prose-and-licenceref",
     re.compile(r"\b" + "Cla" "ude" + r"\s+Code\b|\bLicenseRef-Anthropic-[\w-]+",
                re.IGNORECASE)),
    # The GUID partition-table acronym in PROSE. The existing carve above
    # reaches only the acronym inside a firmware device-path node; the ISO
    # authoring documentation uses it in ordinary sentences. Narrow by
    # construction: the acronym is exempt ONLY when partition/ISO tooling
    # vocabulary sits within 40 characters on one side, so prose uses of the
    # letters elsewhere still hit. The pattern is assembled from pieces so
    # this definition never spells the bare token.
    # Evidence: the 2026-07-26 audit — 6 lines in the ISO-creation runbook.
    ("uefi-partition-table-prose",
     re.compile(r"(?:(?:ESP|El\s+Torito|xorriso|ISO9660|partition|hybrid|report_about)"
                r"[^\n]{0,40}\b" + "G" "P" "T" + r"\b)"
                r"|(?:\b" + "G" "P" "T" + r"\b[^\n]{0,40}"
                r"(?:ESP|El\s+Torito|xorriso|ISO9660|partition|image|boot record|detected))",
                re.IGNORECASE)),
    # The same provider enumeration rendered as a DIAGRAM node or a
    # separator-delimited run (middot / line-break markup), where the
    # owning-company suffix is dropped for width. Exempt a provider name only
    # when a separator of that kind sits immediately beside it, so ordinary
    # prose uses still hit. Evidence: the architecture diagram in the README.
    ("assistant-cloud-scanner-providers-diagram",
     re.compile(r"(?:" + "Cla" "ude" + r"|" + "Gem" "ini" + r"|CoPilot|Chat" "G" "P" "T"
                r"|Grok|" + "Deep" "Seek" + r")"
                r"(?=\s*(?:&middot;|·|<br\s*/?>|&nbsp;))"
                r"|(?<=·)\s*(?:" + "Cla" "ude" + r"|" + "Gem" "ini" + r"|CoPilot"
                r"|Chat" "G" "P" "T" + r"|Grok|" + "Deep" "Seek" + r")",
                re.IGNORECASE)),
    # The provider identifiers as they appear in CONFIGURATION — lowercase,
    # backticked, the literal keys the scanner accepts. A code identifier in
    # its own quoting, not prose. Evidence: the scanner architecture doc lists
    # the six built-in provider keys.
    ("assistant-provider-config-keys",
     re.compile(r"`(?:anthropic|openai|google|microsoft|" + "deep" "seek" +
                r"|xai|local-rules|local-qwen)`")),
    # Vendor homepage URLs in generated third-party notices and package
    # metadata: the host name carries the upstream product name. A URL, not
    # prose. Evidence: the third-party notices file.
    ("vendor-homepage-urls",
     re.compile(r"https?://[\w.-]*(?:" + "cla" "ude" + r"|" + "deep" "seek" +
                r"|o" "pus" + r")[\w./%-]*", re.IGNORECASE)),
    # An SPDX licence identifier whose trailing field coincides with a short
    # internal token (a university-regents clause suffix). Narrow by
    # construction: only the suffix position of a NAMED licence identifier is
    # exempt. Evidence: the third-party notices file, the bundled SPDX licence
    # list, and one package's licence field.
    #
    # The stem alternation is a LIST, not a shape. It used to read
    # `[A-Z][\w.+]*`, which is any capitalised token at all — so the carve
    # exempted the collision after any capitalised word a line happened to
    # carry, while its own comment said "a licence identifier". Measured
    # against the tracked tree, the identifiers that genuinely need this carve
    # are exactly the two stems below; the open shape was covering nothing else
    # it was entitled to cover, and was one capitalised word away from covering
    # a real hit. A new licence identifier joins the alternation on evidence,
    # which is the same rule every other entry in this file follows.
    ("spdx-licence-identifier-suffix",
     re.compile(r"\b(?:BSD-[0-9]-Clause|HPND)-U" "C" + r"\b")),
    # An SPDX licence-EXCEPTION identifier carried in the bundled licence list
    # (config/spdx-license-list.json), beside the licence-identifier-suffix
    # carve above. Hyphens are non-word characters, so a short internal token
    # appearing as a hyphen-delimited fragment of an upstream identifier
    # satisfies the boundary anchor. The identifier is an upstream constant —
    # it cannot be reworded, and dropping it would make the licence gate
    # reject a real licence. Narrow by construction: only this exact quoted
    # identifier is exempt, so prose uses of the letters still hit. The
    # pattern is assembled from pieces so this definition never spells the
    # colliding fragment (same self-reference discipline as the entries
    # above). Evidence: the SPDX licence-identifier gate's data file blocked
    # the 2026-08-05 push on this one line.
    ("spdx-exception-identifier",
     re.compile(r'"GPL-3\.0-389-' + "d" "s" + r'-base-exception"')),
]


def resolve_list_path(override: str | None = None) -> Path:
    """Private term-list path: explicit override, else env var, else default."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    return DEFAULT_LIST_PATH


class ListUnavailable(Exception):
    """Raised when the private term list is missing / unreadable / empty. The
    caller must treat this as a BLOCK (fail-closed), never a pass."""


def load_terms(path: Path) -> list[str]:
    """Load banned terms from the private list. One term per line; blank lines
    and `#` comments ignored. Raises ListUnavailable (fail-closed) if the file
    is absent, unreadable, or yields zero usable terms.

    A term may end in ``*`` — list syntax consumed by compile_terms (the
    digit-tolerant boundary); it is kept on the string here so the marker has
    exactly one reader."""
    if not path.exists():
        raise ListUnavailable(f"term list not found at {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ListUnavailable(f"term list unreadable at {path}: {e}") from e
    terms = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        terms.append(s)
    if not terms:
        raise ListUnavailable(f"term list at {path} contains zero terms")
    return terms


# A multi-token term's SEPARATOR is not part of its identity. The same two
# tokens written with a hyphen, with a space, or run together read as the same
# word, and a list can only spell one of the three — so the two it does not
# spell used to pass. Splitting a term on its separator run and rejoining the
# tokens with this class blocks all three spellings from a single entry.
_TERM_SEPARATOR_RE = re.compile(r"[-\s]+")
_SEPARATOR_VARIANTS = r"[-\s]*"


def term_pattern_body(term: str) -> str:
    """Return the matcher body for one term, with separator variants folded in.

    A single-token term compiles to its escaped literal exactly as before, so
    every one-word entry keeps a byte-identical matcher. A multi-token entry
    becomes its tokens joined by a class accepting a hyphen run, a whitespace
    run, or nothing at all.

    The quantifier is `*`, not `?`, because the no-separator spelling is itself
    one of the variants being closed, and a separator RUN ("a - b", "a  b") is
    the same evasion as a single separator character. Widening stops there: an
    underscore, a dot, or a case-join carries no variant and is NOT matched.

    A term that begins or ends with a separator character falls back to its
    exact escaped spelling — that character is part of the term as written, and
    the fallback can only ever match less than a rejoin, never more.
    """
    tokens = _TERM_SEPARATOR_RE.split(term)
    if len(tokens) < 2 or not tokens[0] or not tokens[-1]:
        return re.escape(term)
    return _SEPARATOR_VARIANTS.join(re.escape(t) for t in tokens)


def compile_terms(terms: list[str]):
    """Compile each term to a case-insensitive, boundary-anchored matcher.

    (?<!\\w) / (?!\\w) is a robust word boundary that also anchors terms whose
    edge character is non-word — so a bare token as a standalone word matches,
    while the same letters inside a larger word do not.

    A term written with a trailing ``*`` gets a DIGIT-TOLERANT trailing
    boundary instead: letters and underscore still end the match, digits do
    not, so ``<term>093`` is a hit. The star is list syntax, never term text.
    Measured 2026-08-24: a coined identifier followed by digits passed
    ``(?!\\w)`` — a digit is a word character — and reached a public remote in
    thirteen commit messages and one ref name. The tolerant boundary is opt-in
    per term because applying it to every term was measured against the whole
    tree and fired on ordinary technical text (a partition specifier such as
    ``hd0,gpt2``, a library named ``sol2``); only coined identifiers have no
    digit-suffixed legitimate use. The leading boundary is never widened: some
    of these tokens are spelled entirely in hex letters, so a widened lead
    would fire inside checksums.
    """
    compiled = []
    for t in terms:
        if t.endswith("*"):
            term, tail = t[:-1], r"(?![A-Za-z_])"
        else:
            term, tail = t, r"(?!\w)"
        pat = re.compile(r"(?<!\w)" + term_pattern_body(term) + tail,
                         re.IGNORECASE)
        compiled.append((term, pat))
    return compiled


def _exempt_spans(line: str):
    spans = []
    for _name, pat in _EXEMPT_SPAN_PATTERNS:
        for m in pat.finditer(line):
            spans.append(m.span())
    return spans


def scan_run(run, compiled_terms):
    """Scan one run of consecutive lines as a single joined string.

    `run` is [(lineno, text), ...] with consecutive line numbers. The lines are
    joined by one space and every term is matched against the JOINED text, so a
    term whose spelling is two words is found even when the author's editor
    wrapped the line between them — the class this gate missed entirely until
    2026-08-25, measured against the real term list.

    Returns [(lineno, line_text, matched_text)] for every non-exempt match, in
    the order the matches occur, where `lineno` is THE LINE THE MATCH STARTS ON.
    A match that begins and ends inside one line yields exactly what per-line
    scanning yielded before, so single-line reporting is unchanged.

    Exemption spans are computed per line, on the author's own bytes, and then
    shifted into joined coordinates; an exemption therefore still covers only
    what it covered before, and never reaches across a join.

    The private TERM is never returned or printed — only the author's own text.
    """
    joined = JoinedText(run)
    exempt = []
    for start, _lineno, text in joined.iter_lines():
        for a, b in _exempt_spans(text):
            exempt.append((start + a, start + b))
    found = []
    for _term, pat in compiled_terms:
        for m in pat.finditer(joined.text):
            s0, s1 = m.span()
            if any(a <= s0 and s1 <= b for (a, b) in exempt):
                continue
            lineno, line_text = joined.locate(s0)
            found.append((s0, lineno, line_text, m.group(0)))
    found.sort(key=lambda f: f[0])
    return [(ln, txt, hit) for _s0, ln, txt, hit in found]


def scan_line(line: str, compiled_terms):
    """Return the matched substrings on `line` that are real violations (a term
    hit not on a trailer line and not covered by an exemption span). The private
    TERM is never returned or printed — only the author's own offending text.

    A single line is a run of one, so this is scan_run with the run it names —
    one definition of what counts as a violation, not two.
    """
    if _TRAILER_RE.search(line):
        return []
    return [hit for _lineno, _text, hit in scan_run([(1, line)], compiled_terms)]


# ---- git range scanning ------------------------------------------------------

def _git(args, cwd):
    # errors="replace" for the same reason the citation checker needs it: a
    # tracked markdown file can carry bytes that are not valid UTF-8, and a
    # strict decode of the diff raised UnicodeDecodeError out of this helper —
    # so the gate CRASHED instead of scanning, and the hook reported a language
    # failure that had not been measured. Nothing is weakened: a byte that is
    # not valid UTF-8 cannot be part of any term this gate searches for.
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, errors="replace",
        cwd=cwd, timeout=30,
    )


def added_lines_from_range(rng: str, cwd: Path):
    """Yield (file, new_lineno, text) for each ADDED line in the diff of `rng`.
    Binary diffs carry no `+` content lines, so they are naturally excluded."""
    res = _git(["diff", "--unified=0", "--no-color", rng], cwd)
    if res.returncode != 0:
        raise RuntimeError(f"git diff {rng} failed: {res.stderr.strip()}")
    cur_file = None
    new_lineno = 0
    for line in res.stdout.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            cur_file = p[2:] if p.startswith("b/") else p  # strip "b/"
            if cur_file == "/dev/null":
                cur_file = None
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            yield (cur_file, new_lineno, line[1:])
            new_lineno += 1


def commit_message_lines(rng: str, cwd: Path):
    """Yield (sha, lineno, text) for each commit-message line in `rng`."""
    res = _git(["log", "--format=%H", rng], cwd)
    if res.returncode != 0:
        raise RuntimeError(f"git log {rng} failed: {res.stderr.strip()}")
    for sha in [s for s in res.stdout.splitlines() if s.strip()]:
        body = _git(["log", "-1", "--format=%B", sha], cwd)
        if body.returncode != 0:
            continue
        for i, line in enumerate(body.stdout.splitlines(), 1):
            yield (sha[:8], i, line)


def _runs_by_file(pairs):
    """Group (file, lineno, text) into per-file runs of consecutive lines.

    Grouping by file first matters: two different files can carry consecutive
    line numbers by coincidence, and joining across that boundary would invent
    an adjacency neither file has.

    A Co-Authored-By trailer is dropped here rather than inside the scan, which
    leaves a gap in the line numbers and so BREAKS the run at that point. That
    is the wanted behaviour — an authorized disclosure line does not become the
    neighbour of the line after it.

    A blank line is dropped for the same reason and with the same effect: a
    paragraph break is not a line wrap, and joining across one would match text
    the author never wrote as a phrase.
    """
    by_file: dict[str, list[tuple[int, str]]] = {}
    order: list[str] = []
    for cur_file, lineno, text in pairs:
        key = cur_file or "?"
        if key not in by_file:
            by_file[key] = []
            order.append(key)
        if _TRAILER_RE.search(text) or not text.strip():
            continue
        by_file[key].append((lineno, text))
    for key in order:
        for run in consecutive_runs(by_file[key]):
            yield key, run


def scan_range(rng: str, compiled_terms, cwd: Path):
    """Scan a push range's added lines + commit messages. Returns a list of
    (location, quoted_line) violations.

    Added lines are scanned as runs of consecutive lines rather than one line at
    a time, so a term split by a line wrap is found; the report names the line
    the match STARTS on, which is where the author has to look. At most one
    report per starting line, as before — a second hit on the same line adds
    nothing a reader can act on.
    """
    violations = []
    for key, run in _runs_by_file(added_lines_from_range(rng, cwd)):
        reported = set()
        for lineno, text, _hit in scan_run(run, compiled_terms):
            if lineno in reported:
                continue
            reported.add(lineno)
            violations.append((f"{key}:{lineno}", text.strip()))

    # A commit message wraps for exactly the same reason a paragraph does, so
    # each message is scanned as one run of its own lines.
    by_commit: dict[str, list[tuple[int, str]]] = {}
    order: list[str] = []
    for sha, lineno, text in commit_message_lines(rng, cwd):
        if sha not in by_commit:
            by_commit[sha] = []
            order.append(sha)
        if _TRAILER_RE.search(text) or not text.strip():
            continue
        by_commit[sha].append((lineno, text))
    for sha in order:
        for run in consecutive_runs(by_commit[sha]):
            reported = set()
            for lineno, text, _hit in scan_run(run, compiled_terms):
                if lineno in reported:
                    continue
                reported.add(lineno)
                violations.append((f"commit {sha} msg:{lineno}", text.strip()))
    return violations


def scan_text(text: str, label: str, compiled_terms):
    """Scan a bare string that is not a file line and not a commit message.

    Added 2026-08-16 for the ref-name gate: a pushed branch or tag NAME is
    published to the public remote exactly like file bytes or a commit message,
    and nothing scanned that namespace — seat-named refs reached both public
    remotes this cycle. There was no way to ask this gate about a string.

    It deliberately reuses scan_line rather than re-implementing the match, so
    there is exactly ONE definition of what counts as a violation. A second
    definition would drift from the first, and the drift would be silent.
    """
    return [(label, hit) for hit in scan_line(text, compiled_terms)]


def main():
    ap = argparse.ArgumentParser(description="Fail-closed public-repo language gate")
    ap.add_argument("--range", dest="rng",
                    help="git range to scan (e.g. origin/dev..HEAD)")
    ap.add_argument("--text", dest="text", action="append", default=None,
                    help="scan a bare string instead of a git range (repeatable); "
                         "used by the pre-push ref-name gate")
    ap.add_argument("--label", dest="label", default="text",
                    help="how to describe a --text subject in output "
                         "(e.g. 'ref name'); one label covers all --text values")
    ap.add_argument("--denylist", dest="denylist",
                    help="override the private term-list path")
    ap.add_argument("--repo", default=None, help="repo root (default: cwd)")
    args = ap.parse_args()

    cwd = Path(args.repo) if args.repo else Path.cwd()
    list_path = resolve_list_path(args.denylist)

    # FAIL-CLOSED: no usable term list => block, naming the path loudly.
    try:
        terms = load_terms(list_path)
    except ListUnavailable as e:
        print(f"[public-language] BLOCK (fail-closed): {e}", file=sys.stderr)
        print(f"[public-language] the private term list is required and must be "
              f"non-empty at:\n    {list_path}\n  (set {ENV_VAR} to override). "
              f"A missing list is never a silent pass.", file=sys.stderr)
        sys.exit(1)

    compiled = compile_terms(terms)

    # --text is its own mode: scan the given strings and nothing else. It is
    # checked BEFORE the no-range branch below, so asking this gate about a ref
    # name never lands in the "nothing to scan" path.
    if args.text:
        violations = []
        for subject in args.text:
            violations.extend(
                scan_text(subject, f"{args.label} {subject}", compiled))
        if violations:
            print(f"[public-language] BLOCK: {len(violations)} banned-language "
                  f"hit(s) in {args.label}:", file=sys.stderr)
            for loc, hit in violations:
                print(f"  {loc}: {hit}", file=sys.stderr)
            print("[public-language] rename to neutral, content-descriptive "
                  "wording (rulebook Rule 22) and re-push.", file=sys.stderr)
            sys.exit(1)
        print(f"[public-language] PASS: no banned-language hits in "
              f"{len(args.text)} {args.label}(s) ({len(terms)} terms).")
        sys.exit(0)

    if not args.rng:
        print("[public-language] no --range given; nothing to scan "
              f"({len(terms)} terms loaded).")
        sys.exit(0)

    try:
        violations = scan_range(args.rng, compiled, cwd)
    except RuntimeError as e:
        print(f"[public-language] BLOCK (fail-closed): {e}", file=sys.stderr)
        sys.exit(1)

    if violations:
        print(f"[public-language] BLOCK: {len(violations)} banned-language "
              f"hit(s) in the push range:", file=sys.stderr)
        for loc, text in violations:
            print(f"  {loc}: {text}", file=sys.stderr)
        print("[public-language] reword to neutral engineering language "
              "(rulebook Rule 22) and re-push.", file=sys.stderr)
        sys.exit(1)

    print(f"[public-language] PASS: no banned-language hits in the push range "
          f"({len(terms)} terms).")
    sys.exit(0)


if __name__ == "__main__":
    main()
