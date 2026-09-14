#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Refuse a release publish whose public claims disagree with its artifacts.

The claim population is read from the documents on every invocation.  Nothing
in this gate carries a list of future features or mirror packages.  It reads:

* the four base-files identity files;
* the image sha256sum record;
* the README download block and every ``Upcoming`` bullet;
* SECURITY.md's project-status line;
* the release-verification rows in docs/release-policy.md;
* the top released CHANGELOG section; and
* every ``pkm install NAME`` command in the supplied switching page, checked
  against the supplied mirror index.

Exit 0 means every mechanically checkable claim above is current.  Exit 2
means stale, incomplete, unreadable, or contradictory input; every finding is
printed as ``file:line`` with the document claim and the artifact truth.

This gate deliberately does not claim semantic understanding of unrestricted
prose.  A free-form future feature with no package name and no matching entry
in a released changelog still needs the release review.  It also does not
fetch or verify served bytes, signatures, URLs, the website, or the wiki; those
are separate release-tail gates over their real consumers.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ISO_RE = re.compile(r"intergenos-r\d{3}(?:\.\d+)?(?:-\d{2})?\.iso", re.I)
PACKAGE_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9+_.-]*$", re.I)
PKM_INSTALL_RE = re.compile(
    r"(?:^|[\s`])(?:sudo\s+)?pkm\s+install\s+([a-z0-9][a-z0-9+_.-]*)",
    re.I,
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    claim: str
    actual: str
    expected: str

    def render(self) -> str:
        return (f"{self.path}:{self.line}: {self.claim}: document says "
                f"{self.actual}; release artifacts say {self.expected}")


def _load_sibling(name: str):
    path = Path(__file__).resolve().with_name(name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return path.name


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_identity(repo: Path, findings: list[Finding]) -> str | None:
    gate = _load_sibling("check-release-identity.py")
    etc = repo / "packages/core/intergenos-base-files/files/etc"
    label = _relative(etc / "igos-release", repo)
    if not etc.is_dir():
        findings.append(Finding(label, 1, "release identity", "directory absent",
                                "four mutually consistent identity files"))
        return None
    try:
        release, _codename, problems = gate.check_etc(etc, "tree base-files")
    except OSError as exc:
        findings.append(Finding(label, 1, "release identity", str(exc),
                                "four readable identity files"))
        return None
    for problem in problems:
        findings.append(Finding(label, 1, "release identity", str(problem),
                                "the four base-files identity files agree"))
    return release


def read_iso_record(path: Path, repo: Path, release: str | None,
                    findings: list[Finding]) -> tuple[str | None, str | None]:
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError as exc:
        findings.append(Finding(display, 1, "ISO sha256 record", str(exc),
                                "one readable sha256sum line for the release image"))
        return None, None
    records: list[tuple[str, str, int]] = []
    for number, line in enumerate(text.splitlines(), 1):
        gnu = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+\.iso)\s*$", line)
        bsd = re.match(r"^SHA256 \((.+\.iso)\) = ([0-9a-fA-F]{64})\s*$", line)
        if gnu:
            records.append((Path(gnu.group(2)).name, gnu.group(1).lower(), number))
        elif bsd:
            records.append((Path(bsd.group(1)).name, bsd.group(2).lower(), number))
    if len(records) != 1:
        findings.append(Finding(display, 1, "ISO sha256 record",
                                f"{len(records)} image rows",
                                "exactly one image row"))
        return None, None
    iso_name, digest, line = records[0]
    if release:
        expected = rf"intergenos-{re.escape(release.lower())}(?:-(?:0[2-9]|[1-9]\d))?\.iso"
        if not re.fullmatch(expected, iso_name):
            findings.append(Finding(display, line, "ISO name", iso_name,
                                    f"intergenos-{release.lower()}.iso or its re-mint ordinal"))
    return iso_name, digest


def read_changelog(repo: Path, release: str | None,
                   findings: list[Finding]) -> str:
    path = repo / "CHANGELOG.md"
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError as exc:
        findings.append(Finding(display, 1, "CHANGELOG top release", str(exc),
                                "a dated, non-empty section for the declared release"))
        return ""
    parser = _load_sibling("preflight-changelog-release-lockstep.py")
    name, _date, body, problem = parser.top_release_section(text)
    heading = re.search(r"^##\s+\[[^\]]+\].*$", text, re.M)
    line = _line_of(text, heading.start()) if heading else 1
    if problem:
        findings.append(Finding(display, line, "CHANGELOG top release", problem,
                                "a parseable release section"))
        return text
    heading = re.search(rf"^##\s+\[{re.escape(name)}\].*$", text, re.M | re.I)
    line = _line_of(text, heading.start()) if heading else line
    if release and name.casefold() != release.casefold():
        findings.append(Finding(display, line, "CHANGELOG top release", name, release))
    if parser.section_is_empty(body):
        findings.append(Finding(display, line, "CHANGELOG release body", "empty",
                                "a non-empty release record"))
    return text


def _readme_download(repo: Path, release: str | None, iso_name: str | None,
                     digest: str | None,
                     findings: list[Finding]) -> None:
    path = repo / "README.md"
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError as exc:
        findings.append(Finding(display, 1, "README download block", str(exc),
                                "a readable release download block"))
        return
    heading = re.search(r"^>\s*###.*Download InterGenOS\s+(R\d{3}(?:\.\d+)?)[^\n]*$",
                        text, re.M | re.I)
    if not heading:
        findings.append(Finding(display, 1, "README download block", "absent",
                                "a block naming the declared release"))
        return
    start_line = _line_of(text, heading.start())
    block_lines = []
    for line in text[heading.start():].splitlines():
        if not line.startswith(">"):
            break
        block_lines.append(line)
    block = "\n".join(block_lines)
    title_release = re.search(r"Download InterGenOS\s+(R\d{3}(?:\.\d+)?)", block, re.I)
    actual_release = title_release.group(1) if title_release else "missing"
    if release and actual_release.casefold() != release.casefold():
        findings.append(Finding(display, start_line, "README release",
                                actual_release, release))

    iso_names = ISO_RE.findall(block)
    actual_iso = iso_names[0] if iso_names else "missing"
    if iso_name and actual_iso.casefold() != iso_name.casefold():
        findings.append(Finding(display, start_line, "README ISO name",
                                actual_iso, iso_name))
    if iso_name:
        for suffix, claim in ((".sha256", "README checksum link"),
                              (".sha256.asc", "README checksum-signature link")):
            expected = iso_name + suffix
            if expected not in block:
                findings.append(Finding(display, start_line, claim, "missing", expected))

    sha_match = re.search(r"sha256[^0-9a-fA-F]*([0-9a-fA-F]{64})", block, re.I)
    actual_sha = sha_match.group(1).lower() if sha_match else "missing"
    if digest and actual_sha != digest:
        line = start_line + (block[:sha_match.start()].count("\n") if sha_match else 0)
        findings.append(Finding(display, line, "README sha256",
                                actual_sha, digest))

def _package_names(repo: Path) -> set[str]:
    names: set[str] = set()
    for recipe in sorted((repo / "packages").glob("*/*/package.yml")):
        try:
            text = _read(recipe)
        except OSError:
            continue
        match = re.search(r"^name:\s*['\"]?([^\s#'\"]+)", text, re.M)
        if match and PACKAGE_TOKEN_RE.fullmatch(match.group(1)):
            names.add(match.group(1).casefold())
    return names


def _upcoming(repo: Path, changelog_text: str, findings: list[Finding]) -> None:
    path = repo / "README.md"
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError:
        return
    section = re.search(r"^##\s+Upcoming\s*$", text, re.M | re.I)
    if not section:
        findings.append(Finding(display, 1, "README Upcoming", "section absent",
                                "a derived set of future claims"))
        return
    next_heading = re.search(r"^##\s+", text[section.end():], re.M)
    end = section.end() + next_heading.start() if next_heading else len(text)
    section_text = text[section.end():end]
    base_line = _line_of(text, section.end())
    bullets: list[tuple[int, str]] = []
    current_line = 0
    current: list[str] = []
    for offset, raw in enumerate(section_text.splitlines(), 1):
        if re.match(r"^-\s+", raw):
            if current:
                bullets.append((current_line, " ".join(current)))
            current_line, current = base_line + offset - 1, [re.sub(r"^-\s+", "", raw)]
        elif current and (raw.startswith("  ") or not raw.strip()):
            if raw.strip():
                current.append(raw.strip())
        elif current:
            bullets.append((current_line, " ".join(current)))
            current = []
    if current:
        bullets.append((current_line, " ".join(current)))

    packages = _package_names(repo)
    released = changelog_text
    unreleased = re.search(r"^##\s+\[Unreleased\].*?(?=^##\s+\[)",
                           released, re.M | re.S | re.I)
    if unreleased:
        released = released[:unreleased.start()] + released[unreleased.end():]
    released_folded = released.casefold()
    for line, bullet in bullets:
        label = re.search(r"\*\*([^*]+)\*\*", bullet)
        inline_names = " ".join(re.findall(r"`([^`]+)`", bullet))
        # The claim subject is the bold lead (or, if absent, the text before
        # the explanatory dash) plus explicit inline-code names.  A package
        # mentioned only as context in the explanation is not itself being
        # promised: "Wayland-capable desktops" must not claim that Wayland is
        # future when the promised subject is the desktop choices.
        subject = ((label.group(1) if label else re.split(r"\s+[—-]\s+", bullet, 1)[0])
                   + " " + inline_names).casefold()
        mentioned = []
        for package in packages:
            pattern = rf"(?<![a-z0-9+_.-]){re.escape(package)}(?![a-z0-9+_.-])"
            spaced = package.replace("-", " ")
            if re.search(pattern, subject) or (spaced != package and
                                               re.search(rf"\b{re.escape(spaced)}\b", subject)):
                mentioned.append(package)
        for package in sorted(mentioned):
            findings.append(Finding(display, line, "README Upcoming package",
                                    f"{package!r} is described as future",
                                    f"the tree already has package {package}"))
        if label:
            phrase = label.group(1).strip().casefold()
            if len(phrase) >= 4 and phrase in released_folded:
                findings.append(Finding(display, line, "README Upcoming feature",
                                        f"{label.group(1)!r} is described as future",
                                        "the same feature is in the released changelog"))


def _security_status(repo: Path, release: str | None,
                     findings: list[Finding]) -> None:
    path = repo / "SECURITY.md"
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError as exc:
        findings.append(Finding(display, 1, "SECURITY status", str(exc),
                                "a readable release-invariant status line"))
        return
    match = re.search(r"^>.*Project status:.*$", text, re.M | re.I)
    if not match:
        findings.append(Finding(display, 1, "SECURITY status", "absent",
                                "a release-invariant status line"))
        return
    line = _line_of(text, match.start())
    status = match.group(0)
    major = re.match(r"(R\d{3})", release or "")
    expected_line = f"{major.group(1)}.x" if major else "RNNN.x"
    exact_points = re.findall(r"R\d{3}\.\d+", status)
    if expected_line not in status or exact_points or "news.html" not in status:
        findings.append(Finding(display, line, "SECURITY release-invariant status",
                                status.strip(),
                                f"the {expected_line} line with the current release delegated to news.html"))


def _policy_rows(repo: Path, release: str | None,
                 findings: list[Finding]) -> None:
    path = repo / "docs/release-policy.md"
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError as exc:
        findings.append(Finding(display, 1, "release-policy version rows", str(exc),
                                "a readable verification table"))
        return
    section = re.search(r"^##\s+Release types\s*$", text, re.M | re.I)
    if not section:
        findings.append(Finding(display, 1, "release-policy version rows",
                                "Release types section absent",
                                "major-release and point-release version rows"))
        return
    next_section = re.search(r"^##\s+", text[section.end():], re.M)
    end = section.end() + next_section.start() if next_section else len(text)
    body = text[section.end():end]
    major = re.search(r"^-\s+\*\*Major releases[^\n]*", body, re.M | re.I)
    point = re.search(r"^-\s+\*\*Point releases[^\n]*", body, re.M | re.I)
    for label, match, examples in (
            ("Major releases", major, ("R001", "R002")),
            ("Point releases", point, ("R001.1", "R001.2"))):
        line = (_line_of(text, section.end() + match.start()) if match
                else _line_of(text, section.start()))
        value = match.group(0) if match else "row absent"
        if match is None or not all(example in value for example in examples) or not (
                "…" in value or "..." in value):
            findings.append(Finding(display, line,
                                    f"release-policy {label} version row",
                                    value.strip(),
                                    f"the invariant examples {', '.join(examples)}, …"))
    if release:
        is_point = "." in release
        selected = point if is_point else major
        if selected is None:
            findings.append(Finding(display, _line_of(text, section.start()),
                                    "release-policy current version class",
                                    release,
                                    "a point-release row" if is_point else "a major-release row"))
    for match in re.finditer(
            r"\b(?:current|latest)\s+release\s+(?:is|=|:)\s+`?(R\d{3}(?:\.\d+)?)`?",
            text, re.I):
        stated = match.group(1)
        if release and stated.casefold() != release.casefold():
            findings.append(Finding(display, _line_of(text, match.start()),
                                    "release-policy current version",
                                    stated, release))


def _mirror_packages(path: Path, repo: Path,
                     findings: list[Finding]) -> set[str] | None:
    display = _relative(path, repo)
    try:
        with path.open("rb") as raw:
            magic = raw.read(2)
        opener = gzip.open if magic == b"\x1f\x8b" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, json.JSONDecodeError, EOFError) as exc:
        findings.append(Finding(display, 1, "mirror index", str(exc),
                                "a readable JSON or gzip-JSON package index"))
        return None
    packages = doc.get("packages")
    if not isinstance(packages, dict):
        findings.append(Finding(display, 1, "mirror index", "packages is not a mapping",
                                "a packages mapping"))
        return None
    if doc.get("package_count") != len(packages):
        findings.append(Finding(display, 1, "mirror index package count",
                                repr(doc.get("package_count")), str(len(packages))))
    return {str(name).casefold() for name in packages}


def _wiki_packages(path: Path, repo: Path, mirror: set[str] | None,
                   findings: list[Finding]) -> int:
    display = _relative(path, repo)
    try:
        text = _read(path)
    except OSError as exc:
        findings.append(Finding(display, 1, "wiki switching page", str(exc),
                                "a readable page"))
        return 0
    commands: dict[str, int] = {}
    for match in PKM_INSTALL_RE.finditer(text):
        commands.setdefault(match.group(1).casefold(), _line_of(text, match.start(1)))
    if not commands:
        findings.append(Finding(display, 1, "wiki switching package claims",
                                "no concrete pkm install commands", "at least one package claim"))
    if mirror is not None:
        for name, line in sorted(commands.items()):
            if name not in mirror:
                findings.append(Finding(display, line, "wiki mirror package",
                                        name, "a package present in the mirror index"))
    return len(commands)


def run(args: argparse.Namespace) -> int:
    repo = Path(args.tree).resolve()
    findings: list[Finding] = []
    release = read_identity(repo, findings)
    iso_name, digest = read_iso_record(Path(args.iso_sha256), repo, release, findings)
    changelog = read_changelog(repo, release, findings)
    _readme_download(repo, release, iso_name, digest, findings)
    _upcoming(repo, changelog, findings)
    _security_status(repo, release, findings)
    _policy_rows(repo, release, findings)
    mirror = _mirror_packages(Path(args.mirror_index), repo, findings)
    wiki_count = _wiki_packages(Path(args.wiki_switching_page), repo, mirror, findings)

    record = (f"release={release or 'unknown'} iso={iso_name or 'unknown'} "
              f"sha256={digest or 'unknown'}")
    scope = ("Scope note: does not cover the README's rounded image size, the CHANGELOG "
             "date (no second source exists in the tree), unrestricted prose without a "
             "package or released-changelog match, served bytes, signatures, URLs, the "
             "website, or rendered wiki.")
    if findings:
        print(f"doc-claims currency gate: REFUSED — {len(findings)} stale or unverifiable claim(s)")
        for finding in findings:
            print(f"  {finding.render()}")
        print(f"  Release record: {record}")
        print(f"  {scope}")
        return 2
    print(f"doc-claims currency gate: PASS ({record}; {wiki_count} wiki package claim(s))")
    print(scope)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", default=".", help="public source-tree root")
    parser.add_argument("--iso-sha256", required=True,
                        help="sha256sum file for the release image")
    parser.add_argument("--mirror-index", required=True,
                        help="generated mirror index (JSON or gzip-JSON)")
    parser.add_argument("--wiki-switching-page", required=True,
                        help="Markdown source for the switching page")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
