#!/usr/bin/env python3
"""Undeclared build-dependency preflight gate (Scan A.2).

Companion to ``preflight-build-order.py`` (Scan A). Where Scan A catches
DECLARED deps that are wired in the wrong build-order position, Scan A.2
catches REAL deps that the upstream build system hard-requires but our
package.yml ``dependencies.build`` does not declare. This is the class
of bug that halted Build #8 at linux-pam (undeclared meson xmllint check)
and Build #9 at rpm 4.18.2 (undeclared PKG_CHECK_MODULES on lua).

Method:
  1. For each package.yml in tree, locate its source[0] tarball in
     ``build/sources/`` (or whatever ``--sources`` points at).
  2. Lazily extract into ``build/scan-cache/<pkg>/`` (cached by tarball
     sha256 so re-runs are fast).
  3. Walk the extracted source for configure.ac, meson.build,
     CMakeLists.txt files.
  4. Parse each for the 5 dep-discovery patterns:
       - ``PKG_CHECK_MODULES([NAME], [pkg-spec], ...)``   autotools/pkg-config
       - ``AC_CHECK_LIB(libname, ...)``                   autotools raw lib
       - ``AC_CHECK_HEADERS([h1 h2 ...], ...)``           autotools raw header
       - ``dependency('name', ...)``                      meson
       - ``find_program('name', ..., required: true)``    meson
       - ``find_package(NAME REQUIRED ...)``              cmake
  5. Normalize each discovered name to our package.yml name via
     ``scripts/pkg-aliases.json`` (pkg-config / header / library / program
     mapping tables) + heuristic transforms (strip ``-N.M`` suffix, strip
     ``lib`` prefix, lowercase).
  6. Check declared deps. If a real dep is undeclared, emit a finding.

Required-vs-soft classification:
  PKG_CHECK_MODULES 4-arg form with non-empty action-if-not-found is SOFT
  (operator handles the absent case); otherwise REQUIRED.
  meson dependency()/find_program() default to ``required: true``; explicit
  ``required: false`` is SOFT.
  AC_CHECK_LIB / AC_CHECK_HEADERS with non-default action-if-found are
  treated as SOFT (operator handles via HAVE_* gates); plain forms HARD.
  cmake find_package(REQUIRED) HARD; without REQUIRED kw is SOFT.

Findings (HARD = exit 1):
  UNDECLARED-PKG-CONFIG-REQUIRED  HARD — PKG_CHECK_MODULES required form
  UNDECLARED-LIB-REQUIRED         HARD — AC_CHECK_LIB plain form
  UNDECLARED-HEADER-REQUIRED      HARD — AC_CHECK_HEADERS plain form
  UNDECLARED-MESON-DEP-REQUIRED   HARD — meson dependency() default-required
  UNDECLARED-MESON-PROGRAM        HARD — meson find_program(required: true)
  UNDECLARED-CMAKE-DEP-REQUIRED   HARD — cmake find_package(REQUIRED)
  UNDECLARED-PKG-CONFIG-SOFT      INFO — PKG_CHECK_MODULES with fallback
  UNDECLARED-MESON-DEP-SOFT       INFO — meson dependency(required: false)
  UNRESOLVED-PKG-NAME             INFO — pkg name can't be mapped; needs alias
  SOURCE-NOT-FOUND                INFO — no source tarball for package; skip
  SOURCE-EXTRACT-FAILED           INFO — tarball can't be extracted; skip

Exit codes:
  0 — clean (no HARD findings)
  1 — HARD findings present (build kickoff should halt)
  2 — environment problem (repo not found, etc.)

Usage:
  scripts/preflight-undeclared-deps.py             # gate mode
  scripts/preflight-undeclared-deps.py --report    # verbose + JSON/TSV artifacts
  scripts/preflight-undeclared-deps.py --root /alt # override repo
  scripts/preflight-undeclared-deps.py --rebuild-cache   # force re-extract

Environment:
  INTERGENOS_ROOT          repo autodetection override
  INTERGENOS_SOURCES_DIR   sources/ dir override (default: <repo>/build/sources)
  INTERGENOS_SCAN_CACHE    scan cache dir override (default: <repo>/build/scan-cache)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


# ----------------------------------------------------------------------------
# Repo + paths
# ----------------------------------------------------------------------------

def discover_repo_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root).resolve()
    env_root = os.environ.get("INTERGENOS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


def sources_dir(repo: Path, arg_sources: str | None) -> Path:
    if arg_sources:
        return Path(arg_sources).resolve()
    env = os.environ.get("INTERGENOS_SOURCES_DIR")
    if env:
        return Path(env).resolve()
    return repo / "build" / "sources"


def scan_cache_dir(repo: Path, arg_cache: str | None) -> Path:
    if arg_cache:
        return Path(arg_cache).resolve()
    env = os.environ.get("INTERGENOS_SCAN_CACHE")
    if env:
        return Path(env).resolve()
    return repo / "build" / "scan-cache"


# ----------------------------------------------------------------------------
# Stdlib YAML for package.yml fields we need
# ----------------------------------------------------------------------------

def detect_build_systems(build_sh: Path, package_yml: dict) -> set[str]:
    """Detect which build system(s) our build.sh actually invokes.

    Returns a subset of {"autotools", "meson", "cmake"}. If build_style is
    declared explicitly in package.yml, that takes precedence.

    Used to avoid reading meson.build when our build.sh uses autotools
    (e.g., git ships both but we use autotools) — limits scanner C from
    docs/research/build_system/preflight_undeclared_deps_v1.md.
    """
    style = (package_yml.get("build_style") or "").strip()
    if style in ("autotools",):
        return {"autotools"}
    if style in ("meson",):
        return {"meson"}
    if style in ("cmake",):
        return {"cmake"}
    # custom / unset: parse build.sh
    found: set[str] = set()
    if build_sh.is_file():
        try:
            content = build_sh.read_text(errors="replace")
        except OSError:
            return {"autotools", "meson", "cmake"}  # fall back: scan all
        if re.search(r"\bmeson\s+setup\b|\bmeson\s+--reconfigure\b|\bmeson\s+configure\b", content):
            found.add("meson")
        if re.search(r"\bcmake\s+\.|\bcmake\s+-B|\bcmake\s+-S", content):
            found.add("cmake")
        if re.search(r"\./configure\b|\bautoreconf\b|\bautoconf\b", content):
            found.add("autotools")
    if not found:
        # No detectable invocation — assume all (conservative)
        found = {"autotools", "meson", "cmake"}
    return found


def parse_pkg_yml(path: Path) -> dict:
    """Minimal package.yml parser — returns dict with name, version,
    source (list), dependencies.build (list).

    Mirrors the indent-style YAML parser in preflight-build-order.py;
    extended to also read source: list and the package name.
    """
    info: dict = {"name": None, "version": None, "build_style": None,
                  "source": [], "dependencies": {"build": []}}
    if not path.is_file():
        return info

    in_deps = False
    in_build = False
    in_source = False
    deps_indent = -1
    build_indent = -1
    source_indent = -1
    cur_source: dict | None = None

    with path.open() as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)

            # Flush-left lines: reset state ONLY when it's a top-level key:
            # value pair. `- url: ...` flush-left is a YAML list item that
            # belongs to whatever section we were in (common in our tree —
            # ~600 package.yml files use this indent style).
            if indent == 0 and not stripped.startswith("- "):
                in_deps = False
                in_build = False
                in_source = False
                deps_indent = -1
                build_indent = -1
                source_indent = -1
                cur_source = None

                if stripped.startswith("name:"):
                    val = stripped[len("name:"):].strip().strip('"').strip("'")
                    info["name"] = val
                elif stripped.startswith("version:"):
                    val = stripped[len("version:"):].strip().strip('"').strip("'")
                    info["version"] = val
                elif stripped.startswith("build_style:"):
                    val = stripped[len("build_style:"):].strip().strip('"').strip("'")
                    info["build_style"] = val
                elif stripped.startswith("dependencies:"):
                    in_deps = True
                    deps_indent = 0
                elif stripped.startswith("source:"):
                    in_source = True
                    source_indent = 0
                continue

            # dependencies.build
            if in_deps:
                if not in_build:
                    if stripped.startswith("build:") and indent > deps_indent:
                        in_build = True
                        build_indent = indent
                    elif indent <= deps_indent:
                        in_deps = False
                    continue
                if indent <= build_indent and not stripped.startswith("- "):
                    in_build = False
                    in_deps = False
                    continue
                if stripped.startswith("- "):
                    item = stripped[2:].strip().strip('"').strip("'")
                    if "#" in item:
                        item = item.split("#", 1)[0].strip()
                    if item:
                        info["dependencies"]["build"].append(item)
                continue

            # source: list of dicts
            if in_source:
                if indent <= source_indent and not stripped.startswith("- "):
                    in_source = False
                    cur_source = None
                    continue
                if stripped.startswith("- "):
                    # New source entry — could be `- url: ...` inline
                    rest = stripped[2:].strip()
                    cur_source = {}
                    info["source"].append(cur_source)
                    if rest.startswith("url:"):
                        cur_source["url"] = rest[len("url:"):].strip().strip('"').strip("'")
                    elif ":" in rest:
                        k, _, v = rest.partition(":")
                        cur_source[k.strip()] = v.strip().strip('"').strip("'")
                elif cur_source is not None and ":" in stripped:
                    k, _, v = stripped.partition(":")
                    cur_source[k.strip()] = v.strip().strip('"').strip("'")
                continue

    return info


# ----------------------------------------------------------------------------
# Aliases
# ----------------------------------------------------------------------------

DEFAULT_ALIASES = {
    "pkg-config": {},
    "header": {},
    "library": {},
    "program": {},
}

# Headers provided by the C runtime + POSIX baseline (glibc, kernel UAPI).
# These are always present in our chroot via toolchain (LFS ch5-7) so they
# don't need to be declared as deps. The scanner silently skips them rather
# than emitting an UNRESOLVED-PKG-NAME finding.
GLIBC_PROVIDED_HEADERS = frozenset({  # noqa: E501
    # Windows-only / cross-platform compatibility headers — silently skipped
    # on linux (these never resolve to a real linux package).
    "io.h", "windows.h", "windef.h", "winsock.h", "winsock2.h",
    "process.h", "direct.h",
    # Non-POSIX but glibc-provided on Linux
    "err.h", "sys/random.h", "sys/sysmacros.h", "sys/syscall.h",
    "sys/sockio.h", "sys/filio.h", "sys/mkdev.h", "termio.h",
    "paths.h", "libintl.h", "ucontext.h", "values.h", "sgtty.h",
    "sys/prctl.h", "sys/personality.h", "sys/eventfd.h", "sys/signalfd.h",
    "sys/timerfd.h", "sys/inotify.h", "sys/fanotify.h", "sys/epoll.h",
    "sys/auxv.h",
    # C89/C99/C11/C17 stdlib
    "assert.h", "ctype.h", "errno.h", "float.h", "inttypes.h", "iso646.h",
    "limits.h", "locale.h", "math.h", "setjmp.h", "signal.h", "stdarg.h",
    "stdbool.h", "stddef.h", "stdint.h", "stdio.h", "stdlib.h", "string.h",
    "tgmath.h", "time.h", "wchar.h", "wctype.h", "complex.h", "fenv.h",
    "stdalign.h", "stdatomic.h", "stdnoreturn.h", "threads.h", "uchar.h",
    # POSIX baseline
    "aio.h", "arpa/inet.h", "cpio.h", "dirent.h", "dlfcn.h", "fcntl.h",
    "fmtmsg.h", "fnmatch.h", "ftw.h", "getopt.h", "glob.h", "grp.h",
    "iconv.h", "langinfo.h", "libgen.h", "monetary.h", "mqueue.h",
    "ndbm.h", "net/if.h", "netdb.h", "netinet/in.h", "netinet/tcp.h",
    "nl_types.h", "poll.h", "pthread.h", "pwd.h", "regex.h", "sched.h",
    "search.h", "semaphore.h", "spawn.h", "strings.h", "stropts.h",
    "sys/file.h", "sys/ioctl.h", "sys/ipc.h", "sys/mman.h", "sys/msg.h",
    "sys/param.h", "sys/poll.h", "sys/queue.h", "sys/resource.h",
    "sys/select.h", "sys/sem.h", "sys/shm.h", "sys/socket.h",
    "sys/stat.h", "sys/statvfs.h", "sys/sysinfo.h", "sys/time.h",
    "sys/times.h", "sys/types.h", "sys/uio.h", "sys/un.h", "sys/utsname.h",
    "sys/wait.h", "syslog.h", "tar.h", "termios.h", "tgmath.h",
    "ulimit.h", "unistd.h", "utime.h", "utmp.h", "utmpx.h", "wordexp.h",
    # Linux UAPI commonly included in glibc-bundled headers
    "endian.h", "byteswap.h", "malloc.h", "alloca.h", "elf.h", "link.h",
    "execinfo.h", "memory.h", "obstack.h", "printf.h",
    "linux/limits.h", "linux/types.h",
    # ASM headers — provided by linux-headers (part of toolchain)
    "asm/types.h", "asm/byteorder.h",
    # Linux kernel UAPI headers — provided by linux-headers in chroot
    "linux/kd.h", "linux/vt.h", "linux/fb.h", "linux/cdrom.h",
    "linux/joystick.h", "linux/input.h", "linux/uinput.h",
    "linux/serial.h", "linux/loop.h", "linux/random.h", "linux/sockios.h",
    "linux/if_packet.h", "linux/if_tun.h", "linux/netlink.h",
    "linux/rtnetlink.h", "linux/genetlink.h", "linux/wireless.h",
    "linux/ethtool.h", "linux/sched.h", "linux/perf_event.h",
    "linux/can.h", "linux/icmp.h", "linux/icmpv6.h", "linux/ip.h",
    "linux/in6.h", "linux/keyctl.h", "linux/kexec.h",
    "linux/blkpg.h", "linux/raid/md_u.h", "linux/raid/md_p.h",
})

# pkg-config / cmake / build-system "builtin" names that don't map to a
# real package — they're satisfied by toolchain or are platform-specific
# (macOS/Windows) that we never build.
BUILTIN_PKG_NAMES = frozenset({
    # cmake's built-in "package" finders that resolve to compiler features
    "Threads", "threads", "PkgConfig", "PythonInterp", "PythonLibs",
    # macOS-only — never satisfied on linux, no dep needed
    "appleframeworks", "AppleFrameworks",
    # Empty / placeholder values from string-construction in meson
    "", "static", "shared",
})

# Programs always present in a chroot via toolchain — no dep declaration
# needed for these in package.yml.
TOOLCHAIN_PROVIDED_PROGRAMS = frozenset({
    "sh", "bash", "make", "cp", "mv", "rm", "ln", "mkdir", "rmdir",
    "chmod", "chown", "cat", "echo", "true", "false", "test", "[",
    "sed", "awk", "grep", "find", "sort", "uniq", "tr", "cut", "head",
    "tail", "wc", "tar", "gzip", "bzip2", "xz", "date", "uname",
    "env", "tee", "xargs", "expr", "basename", "dirname", "id", "stat",
    "install", "tzselect",
    # toolchain
    "cc", "gcc", "g++", "ld", "as", "ar", "ranlib", "strip", "nm",
    "objcopy", "objdump", "readelf",
    "pkg-config", "pkgconf",
})


def load_aliases(repo: Path) -> dict:
    path = repo / "scripts" / "pkg-aliases.json"
    if not path.is_file():
        return DEFAULT_ALIASES
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        print(f"WARN: pkg-aliases.json parse error: {exc}", file=sys.stderr)
        return DEFAULT_ALIASES


def resolve_pkg_config_name(name: str, aliases: dict, known_pkgs: set[str]) -> str | None:
    """Map a pkg-config name to our package.yml name. Returns None if unresolved."""
    # Strip version qualifiers like "= 1.0" or ">= 2.5"
    name = re.split(r"[<>=!]", name)[0].strip()
    if not name:
        return None

    table = aliases.get("pkg-config", {})
    if name in table:
        return table[name]
    if name in known_pkgs:
        return name

    # Heuristic transforms
    candidates: list[str] = []
    # Strip "-N.M" or "-NN" version suffix (e.g. glib-2.0 → glib)
    stripped = re.sub(r"-\d+(\.\d+)*$", "", name)
    if stripped != name:
        candidates.append(stripped)
    # Replace "+" (gtk+ → gtk)
    if "+" in name:
        candidates.append(name.replace("+", ""))
        if stripped != name:
            candidates.append(stripped.replace("+", ""))
    # Common "lib" prefix
    if name.startswith("lib"):
        candidates.append(name[3:])

    for cand in candidates:
        if cand in table:
            return table[cand]
        if cand in known_pkgs:
            return cand

    return None


def resolve_header_name(header: str, aliases: dict) -> str | None:
    table = aliases.get("header", {})
    return table.get(header)


def resolve_library_name(libname: str, aliases: dict, known_pkgs: set[str]) -> str | None:
    table = aliases.get("library", {})
    if libname in table:
        return table[libname]
    # Heuristic: try lib-name as our pkg name
    if libname in known_pkgs:
        return libname
    if ("lib" + libname) in known_pkgs:
        return "lib" + libname
    return None


def resolve_program_name(prog: str, aliases: dict, known_pkgs: set[str]) -> str | None:
    table = aliases.get("program", {})
    if prog in table:
        return table[prog]
    if prog in known_pkgs:
        return prog
    return None


# ----------------------------------------------------------------------------
# Pattern parsers
# ----------------------------------------------------------------------------

# PKG_CHECK_MODULES([NAME], [pkg-spec ...], action-if-found, action-if-not-found)
# We need to balance the m4 brackets [...] to correctly extract args.
# Use a non-greedy regex that grabs the parens content; then split args by
# top-level commas.
PKG_CHECK_MODULES_RE = re.compile(
    r"PKG_CHECK_MODULES\s*\(\s*(.+?)\s*\)\s*(?=\n|$)",
    re.DOTALL,
)

# Simpler: capture the full call's argument string by walking parens.
def _find_unquoted_hash(line: str) -> int:
    """Return the column of the first `#` that is OUTSIDE any quoted
    string on this line, or -1 if no such `#` exists.

    Tracks single + double quotes (toggle on each unescaped quote char).
    """
    in_sq = False
    in_dq = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            i += 2
            continue
        if c == "'" and not in_dq:
            in_sq = not in_sq
        elif c == '"' and not in_sq:
            in_dq = not in_dq
        elif c == "#" and not in_sq and not in_dq:
            return i
        i += 1
    return -1


def _strip_comments(content: str, is_meson: bool) -> str:
    """Replace comments with same-length whitespace so line numbers stay
    aligned, but no macro-call regex matches inside them.

    - autotools (configure.ac): `dnl ...` to EOL, `#` to EOL
    - meson (meson.build): `#` to EOL (no dnl)
    """
    out: list[str] = []
    for line in content.split("\n"):
        comment_starts: list[int] = []
        if not is_meson:
            # autotools: dnl is a comment-starter (at word boundary)
            m = re.search(r"(?:^|\s)dnl\b", line)
            if m:
                comment_starts.append(m.start() if m.group(0) == "dnl" else m.start() + 1)
        idx = _find_unquoted_hash(line)
        if idx >= 0:
            comment_starts.append(idx)
        if comment_starts:
            cut = min(comment_starts)
            line = line[:cut] + " " * (len(line) - cut)
        out.append(line)
    return "\n".join(out)


def find_macro_calls(content: str, macro: str) -> list[tuple[int, str]]:
    """Return [(line_no, arg_string)] for every macro(...) call in content.

    Note: caller is expected to pass content with comments stripped (via
    _strip_comments) when running against autotools/meson sources; the
    regex would otherwise match text inside `#` and `dnl` comments.
    """
    results: list[tuple[int, str]] = []
    pat = re.compile(rf"\b{re.escape(macro)}\s*\(")
    for m in pat.finditer(content):
        start = m.end()
        depth = 1
        i = start
        while i < len(content) and depth > 0:
            c = content[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        if depth == 0:
            arg_str = content[start:i - 1]
            line_no = content.count("\n", 0, m.start()) + 1
            results.append((line_no, arg_str))
    return results


def find_conditional_macro_ranges(content: str) -> list[tuple[int, int]]:
    """Return [(start_line, end_line)] for AS_IF / AS_CASE macro bodies.

    Captures the line range where everything between the macro's opening
    `(` and closing `)` lives. Used by ``is_inside_conditional`` to detect
    autotools m4-level conditionals that don't have the line-based
    `if test ...; then` / `fi` shape (which the simple line tracker
    handles).
    """
    ranges: list[tuple[int, int]] = []
    for macro in ("AS_IF", "AS_CASE"):
        for line_no, arg_str in find_macro_calls(content, macro):
            # arg_str ends at the closing `)` minus 1; figure out its end line
            end_line = line_no + arg_str.count("\n")
            ranges.append((line_no, end_line))
    return ranges


def split_top_level_args(arg_str: str) -> list[str]:
    """Split a macro arg string by top-level commas, honoring [...] m4 quoting
    and (...) nested parens."""
    args: list[str] = []
    cur = []
    depth_paren = 0
    depth_brack = 0
    for c in arg_str:
        if c == "(":
            depth_paren += 1
            cur.append(c)
        elif c == ")":
            depth_paren -= 1
            cur.append(c)
        elif c == "[":
            depth_brack += 1
            cur.append(c)
        elif c == "]":
            depth_brack -= 1
            cur.append(c)
        elif c == "," and depth_paren == 0 and depth_brack == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if cur:
        args.append("".join(cur).strip())
    return args


def strip_m4_brackets(s: str) -> str:
    """Strip outer [...] m4 quote brackets."""
    s = s.strip()
    while s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s


def parse_pkg_check_modules(content: str) -> list[dict]:
    """Return [{line, deps: [pkg-spec, ...], required: bool}]."""
    findings = []
    for line_no, arg_str in find_macro_calls(content, "PKG_CHECK_MODULES"):
        args = split_top_level_args(arg_str)
        if len(args) < 2:
            continue
        # args[0] = VAR name; args[1] = pkg-spec; args[2] = action-if-found
        # args[3] = action-if-not-found
        deps_raw = strip_m4_brackets(args[1])
        # Pkg-config dep list separated by whitespace
        deps: list[str] = []
        for token in re.split(r"\s+", deps_raw):
            token = token.strip().strip("[").strip("]").strip("\\").strip()
            if not token:
                continue
            # Skip version operators (>=, =, <=, etc), bare versions
            if re.match(r"^[<>=!]+$", token) or re.match(r"^[\d.]+$", token):
                continue
            # Skip unexpanded autoconf variables ($GLIB_REQUIRED etc)
            if token.startswith("$"):
                continue
            # Skip pure punctuation/special tokens
            if not re.search(r"\w", token):
                continue
            deps.append(token)
        # Required if 4-arg form is absent OR 4th arg is empty/[]/AC_MSG_ERROR
        required = True
        if len(args) >= 4:
            fourth = strip_m4_brackets(args[3]).strip()
            if fourth and "AC_MSG_ERROR" not in fourth:
                # Has a non-error fallback path = soft
                required = False
        findings.append({"line": line_no, "deps": deps, "required": required})
    return findings


def parse_ac_check_lib(content: str) -> list[dict]:
    """AC_CHECK_LIB(libname, function, [action-if-found], [action-if-not-found]).

    Autoconf default behavior (when no action args): adds -lLIBRARY to LIBS
    + defines HAVE_LIBxxx if found; does nothing on failure. This is SOFT —
    the build does not halt. HARD only when 4th arg explicitly calls
    AC_MSG_ERROR (or similar terminating macro).
    """
    findings = []
    for line_no, arg_str in find_macro_calls(content, "AC_CHECK_LIB"):
        args = split_top_level_args(arg_str)
        if len(args) < 2:
            continue
        libname = strip_m4_brackets(args[0]).strip()
        if not libname or not re.match(r"^[\w.-]+$", libname):
            continue
        required = False
        if len(args) >= 4:
            fourth = strip_m4_brackets(args[3]).strip()
            if "AC_MSG_ERROR" in fourth or "AC_MSG_FAILURE" in fourth:
                required = True
        findings.append({"line": line_no, "deps": [libname], "required": required})
    return findings


def parse_ac_check_headers(content: str) -> list[dict]:
    """AC_CHECK_HEADERS([h1.h h2.h ...], [action-if-found], [action-if-not-found]).

    Default behavior: defines HAVE_H1_H if present; downstream code uses
    #ifdef HAVE_H1_H to gate features. Soft by default. HARD only when 3rd
    arg (action-if-not-found) explicitly errors.
    """
    findings = []
    for line_no, arg_str in find_macro_calls(content, "AC_CHECK_HEADERS"):
        args = split_top_level_args(arg_str)
        if len(args) < 1:
            continue
        headers_raw = strip_m4_brackets(args[0])
        headers = [h.strip() for h in re.split(r"\s+", headers_raw)
                   if h.strip() and h.strip() != "\\"
                   and re.match(r"^[\w][\w./+-]*\.h$", h.strip())]
        required = False
        if len(args) >= 3:
            third = strip_m4_brackets(args[2]).strip()
            if "AC_MSG_ERROR" in third or "AC_MSG_FAILURE" in third:
                required = True
        for h in headers:
            findings.append({"line": line_no, "deps": [h], "required": required})
    return findings


# meson: dependency('name', version: '>=1.0', required: true|false|<feature>, ...)
def _meson_required(arg_str: str) -> bool:
    """Return True only when required: is absent or literal `true`.

    Conditional values (e.g., ``required: feature_nis``, ``required:
    get_option('with_foo')``) classify as SOFT because the operator
    controls them via meson options — those don't HARD-halt configure
    unless the operator opts in.
    """
    m = re.search(r"required\s*:\s*([^,)]+)", arg_str)
    if not m:
        return True  # default required: true
    val = m.group(1).strip()
    return val == "true"


def parse_meson_dependency(content: str) -> list[dict]:
    findings = []
    for line_no, arg_str in find_macro_calls(content, "dependency"):
        m = re.match(r"\s*['\"]([\w.+\-]+)['\"]", arg_str)
        if not m:
            continue
        name = m.group(1)
        required = _meson_required(arg_str)
        findings.append({"line": line_no, "deps": [name], "required": required})
    return findings


def parse_meson_find_program(content: str) -> list[dict]:
    findings = []
    for line_no, arg_str in find_macro_calls(content, "find_program"):
        m = re.match(r"\s*['\"]([\w.+\-]+)['\"]", arg_str)
        if not m:
            continue
        name = m.group(1)
        # Skip meson path-operator pattern: find_program('tools' / 'script.sh')
        # — first quoted arg is a directory, second is the actual program
        # (which is a relative script path, not a system program we'd declare).
        # If `/` appears between two quoted strings near the start, skip.
        after_first = arg_str[m.end():].lstrip()
        if after_first.startswith("/"):
            continue
        required = _meson_required(arg_str)
        findings.append({"line": line_no, "deps": [name], "required": required})
    return findings


# Conditional-block tracking. Walks the source content to detect whether a
# given line is inside an `if/fi` (autotools shell) or `if/endif` (meson)
# block. Limitation: only tracks line-based `if`/`fi`; AS_IF/AS_CASE m4
# macros are not tracked separately (their bodies parse as nested macro
# calls within parens, which we already walk via find_macro_calls).
SHELL_IF_RE = re.compile(r"^\s*(?:if|elif)\b[^#]*$")
SHELL_FI_RE = re.compile(r"^\s*fi\s*(?:#.*)?$")
MESON_IF_RE = re.compile(r"^\s*(?:if|elif)\b[^#]*$")
MESON_ENDIF_RE = re.compile(r"^\s*endif\s*(?:#.*)?$")


def is_inside_conditional(content: str, target_line: int, is_meson: bool,
                          m4_ranges: list[tuple[int, int]] | None = None) -> bool:
    """Return True iff target_line is inside an if/(end|f)i block, or
    (autotools only) inside an AS_IF / AS_CASE macro body.

    Walks line-by-line from the start of the file to target_line, tracking
    the open/close depth of `if`/`fi` (autotools) or `if`/`endif` (meson)
    constructs. The PKG_CHECK_MODULES (or other macro) call at target_line
    is considered "inside" if depth > 0 at that line.

    For autotools: also accepts a precomputed list of m4 conditional macro
    ranges (AS_IF, AS_CASE) and returns True if target_line falls inside
    any of them.
    """
    # First check m4 macro ranges (autotools)
    if not is_meson and m4_ranges:
        for start, end in m4_ranges:
            if start <= target_line <= end:
                return True
    depth = 0
    for ln, line in enumerate(content.split("\n"), start=1):
        if ln >= target_line:
            return depth > 0
        if not is_meson:
            if SHELL_IF_RE.match(line):
                if re.match(r"^\s*if\b", line):
                    depth += 1
            elif SHELL_FI_RE.match(line):
                if depth > 0:
                    depth -= 1
        else:
            if MESON_IF_RE.match(line):
                if re.match(r"^\s*if\b", line):
                    depth += 1
            elif MESON_ENDIF_RE.match(line):
                if depth > 0:
                    depth -= 1
    return depth > 0


# cmake: find_package(NAME [version] REQUIRED ...)
FIND_PACKAGE_RE = re.compile(
    r"find_package\s*\(\s*(\w+)([^)]*)\)",
    re.IGNORECASE,
)

def parse_cmake_find_package(content: str) -> list[dict]:
    findings = []
    for m in FIND_PACKAGE_RE.finditer(content):
        name = m.group(1)
        rest = m.group(2)
        required = bool(re.search(r"\bREQUIRED\b", rest, re.IGNORECASE))
        line_no = content.count("\n", 0, m.start()) + 1
        findings.append({"line": line_no, "deps": [name], "required": required})
    return findings


# ----------------------------------------------------------------------------
# Source-cache management
# ----------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def find_source_tarball(sources: Path, pkg_info: dict) -> Path | None:
    """Locate the source[0] tarball for a package using the URL filename."""
    src_list = pkg_info.get("source") or []
    if not src_list:
        return None
    src0 = src_list[0]
    # Resolve ${version} / ${version_major_minor} in url+filename
    version = pkg_info.get("version") or ""
    vmm = ".".join(version.split(".")[:2]) if version else ""
    fname = src0.get("filename")
    url = src0.get("url", "")

    def subst(s: str) -> str:
        return (s.replace("${version}", version)
                 .replace("${version_major_minor}", vmm))

    if fname:
        candidate = sources / subst(fname)
        if candidate.is_file():
            return candidate
    if url:
        # filename is the last path component
        url_resolved = subst(url)
        url_fname = url_resolved.split("?")[0].rstrip("/").split("/")[-1]
        candidate = sources / url_fname
        if candidate.is_file():
            return candidate

    # Last-ditch glob: <pkg>-<version>.tar.* (case-insensitive)
    pkg_name = pkg_info.get("name") or ""
    if pkg_name and version:
        for ext in (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip", ".tar.lz"):
            cand = sources / f"{pkg_name}-{version}{ext}"
            if cand.is_file():
                return cand
    return None


# Selective-extract: only pull build-system files we actually parse.
# Cuts cache size + extract time by 50-100x vs full-tarball extraction.
WANTED_FILE_NAMES = {
    "configure.ac",
    "configure.in",         # legacy autotools alias
    "meson.build",
    "meson_options.txt",
    "meson.options",        # newer meson option-file name
    "CMakeLists.txt",
}


def _wanted_member(name: str) -> bool:
    """True if path's basename matches a build-system file we parse."""
    base = name.rsplit("/", 1)[-1]
    return base in WANTED_FILE_NAMES


def ensure_extracted(tarball: Path, cache_root: Path, pkg_name: str,
                     rebuild: bool = False) -> tuple[Path | None, str | None]:
    """Extract ONLY build-system files (configure.ac, meson.build,
    CMakeLists.txt, meson_options.txt) into cache_root/<pkg_name>/.

    Cuts cache footprint from ~100MB/pkg (full tree) to <100KB/pkg.
    Returns (extracted_root, error). extracted_root is the top-level dir
    created by the tarball.
    """
    pkg_cache = cache_root / pkg_name
    stamp_file = pkg_cache / ".sha256"
    expected_sha = _sha256_file(tarball)

    if pkg_cache.is_dir() and stamp_file.is_file() and not rebuild:
        if stamp_file.read_text().strip() == expected_sha:
            for entry in pkg_cache.iterdir():
                if entry.is_dir():
                    return entry, None
            return pkg_cache, None

    if pkg_cache.exists():
        shutil.rmtree(pkg_cache)
    pkg_cache.mkdir(parents=True, exist_ok=True)

    try:
        suffix = "".join(tarball.suffixes).lower()
        if suffix.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(tarball) as zf:
                wanted = [n for n in zf.namelist() if _wanted_member(n)]
                for name in wanted:
                    zf.extract(name, pkg_cache)
        elif suffix.endswith(".tar.lz"):
            # lzip: use subprocess + tar's --wildcards to extract selectively
            cmd = ["tar", "--lzip", "-xf", str(tarball), "-C", str(pkg_cache),
                   "--wildcards"]
            for n in WANTED_FILE_NAMES:
                cmd.append(f"*/{n}")
            # Tar exits 0 even if no matches found
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0 and b"Not found in archive" not in res.stderr:
                return None, f"extract-failed (lzip): {res.stderr.decode(errors='replace')[:200]}"
        else:
            with tarfile.open(tarball) as tf:
                # Walk members; extract only wanted basenames
                members = [m for m in tf.getmembers()
                           if m.isfile() and _wanted_member(m.name)]
                for m in members:
                    tf.extract(m, pkg_cache)
    except Exception as exc:
        return None, f"extract-failed: {exc}"

    stamp_file.write_text(expected_sha)

    subdirs = [p for p in pkg_cache.iterdir() if p.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0], None
    if not subdirs:
        # No build-system files found in tarball — return cache dir
        # so scan_package can record "no findings" rather than error
        return pkg_cache, None
    return pkg_cache, None


# ----------------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------------

def scan_package(pkg_name: str, pkg_info: dict, sources: Path, cache: Path,
                 aliases: dict, known_pkgs: set[str], packages_dir: Path,
                 rebuild_cache: bool = False) -> list[dict]:
    """Scan one package; return findings list."""
    declared = set(pkg_info.get("dependencies", {}).get("build", []))
    findings: list[dict] = []

    tarball = find_source_tarball(sources, pkg_info)
    if tarball is None:
        return [{"type": "SOURCE-NOT-FOUND", "consumer": pkg_name}]

    extracted, err = ensure_extracted(tarball, cache, pkg_name, rebuild_cache)
    if err:
        return [{"type": "SOURCE-EXTRACT-FAILED", "consumer": pkg_name,
                 "error": err, "tarball": str(tarball)}]

    # Build-system detection (Limit C): only scan buildfiles for systems
    # we actually invoke per build.sh / build_style.
    build_sh = None
    for tier in packages_dir.iterdir():
        if not tier.is_dir():
            continue
        cand = tier / pkg_name / "build.sh"
        if cand.is_file():
            build_sh = cand
            break
    if build_sh is None:
        active_bs = {"autotools", "meson", "cmake"}  # fall back: scan all
    else:
        active_bs = detect_build_systems(build_sh, pkg_info)

    # File discovery
    configure_ac = None
    meson_builds: list[Path] = []
    cmake_lists: list[Path] = []
    for root, dirs, files in os.walk(extracted):
        # Skip vendored deps + test fixtures dirs to reduce noise
        dirs[:] = [d for d in dirs if d not in
                   ("vendor", "third_party", "thirdparty", "subprojects",
                    ".git", "tests", "test", "examples", "docs",
                    "manual tests")]
        rp = Path(root)
        for f in files:
            if f == "configure.ac" and configure_ac is None:
                # Prefer top-level configure.ac
                configure_ac = rp / f
            elif f == "configure.ac":
                # Take the one with shortest path (closest to root)
                if len(str(rp / f)) < len(str(configure_ac)):
                    configure_ac = rp / f
            elif f == "meson.build":
                meson_builds.append(rp / f)
            elif f == "CMakeLists.txt":
                cmake_lists.append(rp / f)

    # Limit meson + cmake to top-most file to avoid sub-tree noise
    if meson_builds:
        meson_builds = [min(meson_builds, key=lambda p: len(str(p)))]
    if cmake_lists:
        cmake_lists = [min(cmake_lists, key=lambda p: len(str(p)))]

    # Limit C application: skip buildfiles whose build-system we don't use
    if "autotools" not in active_bs:
        configure_ac = None
    if "meson" not in active_bs:
        meson_builds = []
    if "cmake" not in active_bs:
        cmake_lists = []

    discoveries: list[tuple[str, str, dict]] = []  # (build_sys, finding_class, finding)

    # We need source content for is_inside_conditional. Cache per-file.
    file_contents: dict[Path, str] = {}

    def read(file_path: Path) -> str:
        if file_path not in file_contents:
            try:
                file_contents[file_path] = file_path.read_text(errors="replace")
            except OSError:
                file_contents[file_path] = ""
        return file_contents[file_path]

    if configure_ac is not None:
        raw_content = read(configure_ac)
        content = _strip_comments(raw_content, is_meson=False)
        m4_ranges = find_conditional_macro_ranges(content)
        for f in parse_pkg_check_modules(content):
            cond = is_inside_conditional(content, f["line"], is_meson=False, m4_ranges=m4_ranges)
            for dep in f["deps"]:
                if cond:
                    ftype = "UNDECLARED-PKG-CONFIG-CONDITIONAL"
                else:
                    ftype = ("UNDECLARED-PKG-CONFIG-REQUIRED" if f["required"]
                             else "UNDECLARED-PKG-CONFIG-SOFT")
                discoveries.append((
                    "autotools", ftype,
                    {"file": str(configure_ac.relative_to(extracted)),
                     "line": f["line"], "dep_raw": dep, "kind": "pkg-config"},
                ))
        for f in parse_ac_check_lib(content):
            cond = is_inside_conditional(content, f["line"], is_meson=False, m4_ranges=m4_ranges)
            for dep in f["deps"]:
                if cond:
                    ftype = "UNDECLARED-LIB-CONDITIONAL"
                else:
                    ftype = ("UNDECLARED-LIB-REQUIRED" if f["required"]
                             else "UNDECLARED-LIB-SOFT")
                discoveries.append((
                    "autotools", ftype,
                    {"file": str(configure_ac.relative_to(extracted)),
                     "line": f["line"], "dep_raw": dep, "kind": "library"},
                ))
        for f in parse_ac_check_headers(content):
            cond = is_inside_conditional(content, f["line"], is_meson=False, m4_ranges=m4_ranges)
            for dep in f["deps"]:
                if cond:
                    ftype = "UNDECLARED-HEADER-CONDITIONAL"
                else:
                    ftype = ("UNDECLARED-HEADER-REQUIRED" if f["required"]
                             else "UNDECLARED-HEADER-SOFT")
                discoveries.append((
                    "autotools", ftype,
                    {"file": str(configure_ac.relative_to(extracted)),
                     "line": f["line"], "dep_raw": dep, "kind": "header"},
                ))

    for mfile in meson_builds:
        raw_content = read(mfile)
        content = _strip_comments(raw_content, is_meson=True)
        for f in parse_meson_dependency(content):
            cond = is_inside_conditional(content, f["line"], is_meson=True)
            for dep in f["deps"]:
                if cond:
                    ftype = "UNDECLARED-MESON-DEP-CONDITIONAL"
                else:
                    ftype = ("UNDECLARED-MESON-DEP-REQUIRED" if f["required"]
                             else "UNDECLARED-MESON-DEP-SOFT")
                discoveries.append((
                    "meson", ftype,
                    {"file": str(mfile.relative_to(extracted)),
                     "line": f["line"], "dep_raw": dep, "kind": "pkg-config"},
                ))
        for f in parse_meson_find_program(content):
            if not f["required"]:
                continue  # operator opted out
            cond = is_inside_conditional(content, f["line"], is_meson=True)
            for dep in f["deps"]:
                ftype = ("UNDECLARED-MESON-PROGRAM-CONDITIONAL" if cond
                         else "UNDECLARED-MESON-PROGRAM")
                discoveries.append((
                    "meson", ftype,
                    {"file": str(mfile.relative_to(extracted)),
                     "line": f["line"], "dep_raw": dep, "kind": "program"},
                ))

    for cfile in cmake_lists:
        raw_content = read(cfile)
        content = _strip_comments(raw_content, is_meson=False)  # cmake # comments
        for f in parse_cmake_find_package(content):
            if not f["required"]:
                continue
            # cmake doesn't have a directly-analogous if-block syntax that
            # we'd see in a CMakeLists; the find_package call is usually
            # top-level. Use shell-style heuristic as a rough check.
            cond = is_inside_conditional(content, f["line"], is_meson=False)
            for dep in f["deps"]:
                ftype = ("UNDECLARED-CMAKE-DEP-CONDITIONAL" if cond
                         else "UNDECLARED-CMAKE-DEP-REQUIRED")
                discoveries.append((
                    "cmake", ftype,
                    {"file": str(cfile.relative_to(extracted)),
                     "line": f["line"], "dep_raw": dep, "kind": "pkg-config"},
                ))

    # Resolve names + filter declared
    for _bs, ftype, fdata in discoveries:
        raw = fdata["dep_raw"]
        kind = fdata["kind"]

        # Skip toolchain/libc/builtin names that don't correspond to a
        # declarable dep. Silent — no finding emitted.
        if kind == "header" and raw in GLIBC_PROVIDED_HEADERS:
            continue
        if kind == "pkg-config" and raw in BUILTIN_PKG_NAMES:
            continue
        if kind == "program" and raw in TOOLCHAIN_PROVIDED_PROGRAMS:
            continue

        if kind == "pkg-config":
            resolved = resolve_pkg_config_name(raw, aliases, known_pkgs)
        elif kind == "header":
            resolved = resolve_header_name(raw, aliases)
        elif kind == "library":
            resolved = resolve_library_name(raw, aliases, known_pkgs)
        elif kind == "program":
            resolved = resolve_program_name(raw, aliases, known_pkgs)
        else:
            resolved = None

        if resolved is None:
            findings.append({
                "type": "UNRESOLVED-PKG-NAME",
                "consumer": pkg_name,
                "dep_raw": raw,
                "kind": kind,
                "file": fdata["file"],
                "line": fdata["line"],
            })
            continue

        # Limit D: multi-package pkg-config providers. If alias value is a
        # list, declared-check passes if ANY listed name is in declared.
        if isinstance(resolved, list):
            candidates = resolved
            if any(c in declared or c == pkg_name for c in candidates):
                continue
            if any(c == "glibc" for c in candidates):
                continue  # one of the alternates is glibc-provided
            # Not declared via any alternate — emit with the first as
            # canonical dep name (operator picks which to add).
            resolved_str = candidates[0]
        else:
            resolved_str = resolved
            if resolved_str in declared or resolved_str == pkg_name:
                continue
            if resolved_str == "glibc":
                continue

        findings.append({
            "type": ftype,
            "consumer": pkg_name,
            "dep": resolved_str,
            "dep_alternates": resolved if isinstance(resolved, list) else None,
            "dep_raw": raw,
            "kind": kind,
            "file": fdata["file"],
            "line": fdata["line"],
        })

    return findings


def scan(repo: Path, sources: Path, cache: Path, aliases: dict,
         rebuild_cache: bool = False, only: list[str] | None = None,
         progress: bool = False) -> list[dict]:
    pkgs_dir = repo / "packages"
    all_findings: list[dict] = []

    # Inventory all packages
    pkg_paths: dict[str, Path] = {}  # name -> package.yml path
    for tier in pkgs_dir.iterdir():
        if not tier.is_dir():
            continue
        for pkg_dir in tier.iterdir():
            if not pkg_dir.is_dir():
                continue
            yml = pkg_dir / "package.yml"
            if yml.is_file():
                pkg_paths[pkg_dir.name] = yml

    known_pkgs = set(pkg_paths.keys())

    selected = sorted(known_pkgs)
    if only:
        selected = [p for p in selected if p in only]

    cache.mkdir(parents=True, exist_ok=True)

    total = len(selected)
    for i, pkg_name in enumerate(selected, start=1):
        if progress and i % 25 == 0:
            print(f"  [{i}/{total}] scanned", file=sys.stderr)
        pkg_info = parse_pkg_yml(pkg_paths[pkg_name])
        pkg_info["name"] = pkg_info.get("name") or pkg_name
        try:
            pkg_findings = scan_package(pkg_name, pkg_info, sources, cache,
                                        aliases, known_pkgs, pkgs_dir,
                                        rebuild_cache)
        except Exception as exc:
            pkg_findings = [{"type": "SCAN-ERROR", "consumer": pkg_name,
                             "error": str(exc)}]
        all_findings.extend(pkg_findings)

    return all_findings


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

# Gate-blocking findings (exit 1). PKG_CHECK_MODULES + meson + cmake have
# clean halt-vs-soft signal in v1: AC_MSG_ERROR / required:true / REQUIRED
# keyword are unambiguous. AC_CHECK_LIB / AC_CHECK_HEADERS detection is
# kept but classified as INFO due to AS_IF-conditional false-positive risk
# (e.g., rpm wraps libcap checks in `AS_IF([test "$with_cap" = yes], ...)`;
# our build.sh's --without-cap suppresses the check at runtime, but the
# scanner doesn't track conditional context yet). v2 adds AS_IF awareness
# to escalate autotools-lib/header findings back to HARD.
HARD_TYPES = frozenset({
    "UNDECLARED-PKG-CONFIG-REQUIRED",
    "UNDECLARED-MESON-DEP-REQUIRED",
    "UNDECLARED-MESON-PROGRAM",
    "UNDECLARED-CMAKE-DEP-REQUIRED",
})


def emit_summary(findings: list[dict], verbose: bool) -> None:
    by_type: dict[str, list[dict]] = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    hard_count = sum(len(by_type.get(t, [])) for t in HARD_TYPES)
    total_count = len(findings)

    print("=== preflight-undeclared-deps (Scan A.2) ===")
    print(f"TOTAL FINDINGS: {total_count}")
    print(f"HARD FINDINGS:  {hard_count}")
    print()

    if not findings:
        print("PASS — no undeclared build-deps detected.")
        return

    for t in sorted(by_type.keys()):
        count = len(by_type[t])
        marker = "HARD" if t in HARD_TYPES else "INFO"
        print(f"  [{marker}] {t}: {count}")

    if verbose:
        print()
        print("=== Findings (all) ===")
    else:
        print()
        print("=== First 10 of each HARD finding type ===")

    for t in sorted(by_type.keys()):
        items = by_type[t]
        if not items:
            continue
        if not verbose and t not in HARD_TYPES:
            continue
        print(f"\n[{t}]")
        show = items if verbose else items[:10]
        for f in show:
            consumer = f.get("consumer", "?")
            dep = f.get("dep", f.get("dep_raw", "?"))
            file_ = f.get("file", "?")
            line = f.get("line", "?")
            kind = f.get("kind", "")
            print(f"  {consumer}: needs {dep} (kind={kind}, {file_}:{line})")
        if not verbose and len(items) > 10:
            print(f"  ... ({len(items) - 10} more)")

    if hard_count > 0:
        print()
        print("FAIL — undeclared HARD build-deps detected. Resolve by adding "
              "the missing dep to dependencies.build in the relevant "
              "package.yml, OR (if upstream's check is genuinely optional) "
              "by adding the alias to scripts/pkg-aliases.json mapping to "
              "an in-tree pkg, OR (if intentional) suppressing via the "
              "scanner's per-package allowlist (TBD).")


def write_artifacts(repo: Path, findings: list[dict], ts: str) -> tuple[Path, Path]:
    build_dir = repo / "build"
    build_dir.mkdir(exist_ok=True)
    json_path = build_dir / f"preflight-undeclared-deps-{ts}.json"
    tsv_path = build_dir / f"preflight-undeclared-deps-{ts}.tsv"
    json_path.write_text(json.dumps({
        "timestamp": ts,
        "findings": findings,
    }, indent=2))
    with tsv_path.open("w") as fp:
        fp.write("type\tconsumer\tdep\tdep_raw\tkind\tfile\tline\n")
        for f in findings:
            fp.write("\t".join([
                f.get("type", ""),
                f.get("consumer", ""),
                f.get("dep", ""),
                f.get("dep_raw", ""),
                f.get("kind", ""),
                f.get("file", ""),
                str(f.get("line", "")),
            ]) + "\n")
    return json_path, tsv_path


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Preflight undeclared-build-dependency gate (Scan A.2).",
        epilog="Exit 0 on clean (no HARD findings), 1 on HARD findings, 2 on env.",
    )
    ap.add_argument("--root", help="repo root (overrides INTERGENOS_ROOT)")
    ap.add_argument("--sources", help="sources/ dir (default: <repo>/build/sources)")
    ap.add_argument("--cache", help="scan cache dir (default: <repo>/build/scan-cache)")
    ap.add_argument("--report", action="store_true",
                    help="also write JSON + TSV artifacts to <repo>/build/")
    ap.add_argument("--verbose", action="store_true",
                    help="show all findings (incl. INFO + soft); default is HARD only top-10")
    ap.add_argument("--rebuild-cache", action="store_true",
                    help="force re-extract of source tarballs (slow)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict scan to listed package names")
    ap.add_argument("--progress", action="store_true",
                    help="print progress to stderr every 25 packages")
    args = ap.parse_args()

    repo = discover_repo_root(args.root)
    if not (repo / "scripts").is_dir() or not (repo / "packages").is_dir():
        print(f"ERROR: repo root {repo} missing scripts/ or packages/", file=sys.stderr)
        return 2

    sources = sources_dir(repo, args.sources)
    cache = scan_cache_dir(repo, args.cache)
    aliases = load_aliases(repo)

    if not sources.is_dir():
        print(f"WARN: sources dir {sources} absent — most packages will SKIP.", file=sys.stderr)

    findings = scan(repo, sources, cache, aliases,
                    rebuild_cache=args.rebuild_cache,
                    only=args.only,
                    progress=args.progress)
    emit_summary(findings, args.verbose)

    if args.report:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path, tsv_path = write_artifacts(repo, findings, ts)
        print()
        print(f"Report artifacts:")
        print(f"  JSON: {json_path}")
        print(f"  TSV:  {tsv_path}")

    hard_count = sum(1 for f in findings if f["type"] in HARD_TYPES)
    return 0 if hard_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
