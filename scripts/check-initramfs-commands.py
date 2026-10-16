#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed initramfs command gate: every command an init script runs must exist inside its initramfs.

An initramfs /init runs with nothing but what the archive carries. A command
word with no executable behind it is "not found" (exit status 127), and an
init script under `set -e` exits on it, which the kernel reports as
"Attempted to kill init!". Measured 2026-10-16: the R001.3 live medium
panicked on that line on real hardware, because init.sh had gained a chmod
call that the live builder's busybox applet list did not link.

The gate reads the init script itself, takes the first word of every simple
command it can run, and resolves each one INSIDE the initramfs:

  - a function the script defines is skipped;
  - a shell builtin is accepted only when the initramfs's own shell says it
    is one (`type`, run on the archive's busybox with no PATH);
  - any other word must reach a regular, executable file under the
    archive's sbin/, usr/sbin/, bin/ or usr/bin/ (the busybox shell's search
    path when the kernel gives init no PATH), or at its own absolute path;
    a link to the archive's busybox must name an applet that busybox lists;
  - a word built at run time ($cmd, eval, a sourced file) cannot be
    resolved and is refused.

Words reached through `exec`, `command`, `sh -c '<literal>'` (with any
option cluster such as -ec), a `trap` action, a command substitution (also
inside double quotes, inside an unquoted here-document and inside an
arithmetic expansion) are read too. The bodies of quoted here-documents are
data and are not read. `command -v NAME` is a probe, not a call.

A program another one runs is resolved too, and must be an executable file
(a builtin or a function is not one): the word `exec` hands on; the program
a runner names (env, nohup, timeout, nice, setsid, xargs, chroot, flock,
stdbuf, taskset, ionice, time, read by each one's own options); and
busybox's first argument, which must be an applet that busybox lists. A
runner whose program cannot be read (an unknown option, xargs or chroot with
no program, a word built at run time where an option could be), a trap
action built at run time, and a shell that runs a script file or the
commands on a redirected or piped standard input are refused by name.

Usage:
  check-initramfs-commands.py --init SCRIPT --root DIR   [--optional NAME]...
  check-initramfs-commands.py --init SCRIPT --cpio FILE  [--optional NAME]...
  check-initramfs-commands.py --init SCRIPT --list

--root is a staged initramfs work tree (what a builder hands cpio); --cpio is
a built archive (newc, gzip-compressed or not; the first archive of a
concatenated initrd); --list prints the script's command words and stops.
--optional names a program the script may lack, because it probes for it
before the call and the builder bundles it only when present; an absent
optional program is reported by name and does not refuse.

Exit 0 PASS, 1 REFUSED, 2 on usage or read errors.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PROG = "initramfs-commands"

SEARCH_DIRS = ("sbin", "usr/sbin", "bin", "usr/bin")

# Names a POSIX shell may provide itself. Each one the script uses is then
# CONFIRMED against the initramfs's own shell; a name it does not build in
# must resolve as a program like any other word.
BUILTIN_CANDIDATES = frozenset({
    ":", "[", "alias", "bg", "break", "cd", "command", "continue", "echo",
    "exec", "exit", "export", "false", "fg", "getopts", "hash", "jobs",
    "kill", "local", "printf", "pwd", "read", "readonly", "return", "set",
    "shift", "test", "times", "trap", "true", "type", "ulimit", "umask",
    "unalias", "unset", "wait",
})
# Builtins whose arguments are code the gate cannot see.
DYNAMIC_BUILTINS = frozenset({"eval", ".", "source"})
SHELLS = frozenset({"sh", "ash", "bash"})
# Programs that run another program named among their own arguments, each with its option grammar: (options whose
# value is the next word, options with no value, operands before the program, options that mean no program runs).
# A runner's program must be an executable file (a shell builtin or function is not one it can run); an option not
# listed here, or a word built at run time where the program could be, cannot be read, so the runner is refused.
RUNNERS = {
    "env": ({"-u", "--unset", "-C", "--chdir"}, {"-i", "--ignore-environment", "-0", "--null", "-"}, 0, set()),
    "nohup": (set(), set(), 0, set()),
    "timeout": ({"-k", "--kill-after", "-s", "--signal"}, {"--preserve-status", "--foreground", "-v", "--verbose"}, 1, set()),
    "nice": ({"-n", "--adjustment"}, set(), 0, set()),
    "setsid": (set(), {"-c", "--ctty", "-f", "--fork", "-w", "--wait"}, 0, set()),
    "xargs": ({"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "-n", "--max-args", "-P", "--max-procs", "-s",
               "--max-chars"}, {"-0", "--null", "-r", "--no-run-if-empty", "-t", "--verbose", "-x", "--exit", "-p",
                                "--interactive"}, 0, set()),
    "chroot": ({"--userspec", "--groups"}, {"--skip-chdir"}, 1, set()),
    "flock": ({"-w", "--timeout", "-E", "--conflict-exit-code"}, {"-s", "--shared", "-x", "-e", "--exclusive", "-u",
                                                                 "--unlock", "-n", "--nonblock", "-o", "--close", "-F",
                                                                 "--no-fork", "--verbose"}, 1, set()),
    "stdbuf": ({"-i", "-o", "-e", "--input", "--output", "--error"}, set(), 0, set()),
    "taskset": (set(), {"-a", "--all-tasks", "-c", "--cpu-list"}, 1, {"-p", "--pid"}),
    "ionice": ({"-c", "--class", "-n", "--classdata"}, {"-t", "--ignore"}, 0, {"-p", "--pid", "-P", "--pgid", "-u", "--uid"}),
    "time": ({"-f", "--format", "-o", "--output"}, {"-p", "--portability", "-a", "--append", "-v", "--verbose", "-q",
                                                   "--quiet"}, 0, set()),
}
# With no program named these run one of their own choosing (xargs runs echo, chroot the new root's shell), which the
# gate cannot read here.
RUNNERS_WITH_A_DEFAULT = frozenset({"xargs", "chroot"})
STDIN_REDIRECTIONS = frozenset({"<", "<<", "<<-", "<>"})

START_KEYWORDS = frozenset({"if", "then", "else", "elif", "while", "until", "do", "!", "{"})
END_KEYWORDS = frozenset({"fi", "done", "}"})
SEPARATORS = frozenset({";", "&", "&&", "||", "|", "("})
REDIRECTIONS = frozenset({"<", ">", ">>", "<&", ">&", "<>", ">|", "<<", "<<-"})
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


class ParseError(Exception):
    pass


class Tok:
    __slots__ = ("kind", "text", "line", "literal", "dynamic")

    def __init__(self, kind, text, line, literal=None, dynamic=False):
        self.kind = kind        # WORD, OP, NL, IONUM
        self.text = text        # the raw source text
        self.line = line
        self.literal = literal  # WORD: the value after quote removal (None when it holds an expansion)
        self.dynamic = dynamic  # WORD: holds a parameter expansion or a command substitution


def _match_close(src, i, opener):
    """Index just past the construct opened at src[i] (src[i:] starts with opener: '$(', '`', '${', '$((')."""
    n = len(src)
    if opener == "`":
        j = i + 1
        while j < n:
            if src[j] == "\\":
                j += 2
                continue
            if src[j] == "`":
                return j + 1
            j += 1
        raise ParseError("unterminated backquote")
    if opener == "$((":
        depth, j = 2, i + 3
        while j < n and depth:
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
            j += 1
        if depth:
            raise ParseError("unterminated arithmetic expansion")
        return j
    close = ")" if opener == "$(" else "}"
    open_ch = "(" if opener == "$(" else "{"
    depth, j = 1, i + len(opener)
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = src.find("'", j + 1)
            if k < 0:
                raise ParseError("unterminated single quote")
            j = k + 1
            continue
        if c == '"':
            j = _skip_dquote(src, j)
            continue
        if src.startswith("$((", j):
            j = _match_close(src, j, "$((")
            continue
        if src.startswith("$(", j) or src.startswith("${", j):
            j = _match_close(src, j, src[j:j + 2])
            continue
        if c == "`":
            j = _match_close(src, j, "`")
            continue
        if c == open_ch:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ParseError("unterminated %s" % opener)


def _skip_dquote(src, i):
    """Index just past the double-quoted string that starts at src[i] == '"'."""
    j, n = i + 1, len(src)
    while j < n:
        c = src[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        if src.startswith("$((", j):
            j = _match_close(src, j, "$((")
            continue
        if src.startswith("$(", j) or src.startswith("${", j):
            j = _match_close(src, j, src[j:j + 2])
            continue
        if c == "`":
            j = _match_close(src, j, "`")
            continue
        j += 1
    raise ParseError("unterminated double quote")


def _substitutions(text):
    """Every command substitution in the raw text of a word: [(offset, inner text)]."""
    out, j, n = [], 0, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = text.find("'", j + 1)
            j = n if k < 0 else k + 1
            continue
        if c == '"':
            end = _skip_dquote(text, j)
            out.extend((j + 1 + o, s) for o, s in _dq_substitutions(text[j + 1:end - 1]))
            j = end
            continue
        if text.startswith("$((", j):
            # the shell evaluates an arithmetic expansion, running every command substitution inside it
            end = _match_close(text, j, "$((")
            out.extend((j + 3 + o, s) for o, s in _substitutions(text[j + 3:end - 2]))
            j = end
            continue
        if text.startswith("$(", j):
            end = _match_close(text, j, "$(")
            out.append((j + 2, text[j + 2:end - 1]))
            j = end
            continue
        if text.startswith("${", j):
            end = _match_close(text, j, "${")
            out.extend((j + 2 + o, s) for o, s in _substitutions(text[j + 2:end - 1]))
            j = end
            continue
        if c == "`":
            end = _match_close(text, j, "`")
            out.append((j + 1, text[j + 1:end - 1]))
            j = end
            continue
        j += 1
    return out


def _dq_substitutions(text):
    """Command substitutions inside double-quoted text (single quotes are literal there)."""
    out, j, n = [], 0, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if text.startswith("$((", j):
            end = _match_close(text, j, "$((")
            out.extend((j + 3 + o, s) for o, s in _substitutions(text[j + 3:end - 2]))
            j = end
            continue
        if text.startswith("$(", j):
            end = _match_close(text, j, "$(")
            out.append((j + 2, text[j + 2:end - 1]))
            j = end
            continue
        if text.startswith("${", j):
            end = _match_close(text, j, "${")
            out.extend((j + 2 + o, s) for o, s in _substitutions(text[j + 2:end - 1]))
            j = end
            continue
        if c == "`":
            end = _match_close(text, j, "`")
            out.append((j + 1, text[j + 1:end - 1]))
            j = end
            continue
        j += 1
    return out


def tokenize(src, line0=1):
    """Tokens of a shell script, and the command substitutions it holds: (tokens, [(line, inner text)])."""
    toks, subs = [], []
    pending = []  # here-documents whose bodies start after the current line: (delimiter, quoted, strip_tabs)
    i, n, line = 0, len(src), line0
    while i < n:
        c = src[i]
        if c == "\\" and src.startswith("\\\n", i):
            i += 2
            line += 1
            continue
        if c in " \t":
            i += 1
            continue
        if c == "#":
            k = src.find("\n", i)
            i = n if k < 0 else k
            continue
        if c == "\n":
            toks.append(Tok("NL", "\n", line))
            i += 1
            line += 1
            for delim, quoted, strip in pending:
                body = []
                while True:
                    if i >= n:
                        raise ParseError("here-document %r not terminated" % delim)
                    k = src.find("\n", i)
                    k = n if k < 0 else k
                    text = src[i:k]
                    i = k + 1
                    line += 1
                    if (text.lstrip("\t") if strip else text) == delim:
                        break
                    body.append((line - 1, text))
                if not quoted:
                    for bline, text in body:
                        subs.extend((bline, s) for _, s in _dq_substitutions(text))
            pending = []
            continue
        for op in ("<<-", "&&", "||", ";;", "<<", ">>", "<&", ">&", "<>", ">|", ";", "&", "|", "(", ")", "<", ">"):
            if src.startswith(op, i):
                toks.append(Tok("OP", op, line))
                i += len(op)
                if op in ("<<", "<<-"):
                    while i < n and src[i] in " \t":
                        i += 1
                    w, i, wline, wlines = _word(src, i, line)
                    line += wlines
                    if w is None:
                        raise ParseError("line %d: here-document without a delimiter" % line)
                    quoted = any(q in w.text for q in ("'", '"', "\\"))
                    pending.append((w.literal if w.literal is not None else w.text, quoted, op == "<<-"))
                    toks.append(w)
                break
        else:
            w, i, wline, wlines = _word(src, i, line)
            if w is None:
                raise ParseError("line %d: cannot read the word at %r" % (line, src[i:i + 20]))
            if w.text.isdigit() and i < n and src[i] in "<>":
                w.kind = "IONUM"
            toks.append(w)
            for off, inner in _substitutions(w.text):
                subs.append((wline + w.text.count("\n", 0, off), inner))
            line += wlines
    toks.append(Tok("NL", "\n", line))
    return toks, subs


def _word(src, i, line):
    """One word starting at src[i]: (Tok or None, the index past it, its line, the newlines inside it)."""
    n, j = len(src), i
    out, dynamic, start = [], False, i
    while j < n:
        c = src[j]
        if c in " \t\n;&|()<>":
            break
        if c == "\\":
            if j + 1 < n and src[j + 1] == "\n":
                j += 2
                continue
            if j + 1 < n:
                out.append(src[j + 1])
            j += 2
            continue
        if c == "'":
            k = src.find("'", j + 1)
            if k < 0:
                raise ParseError("line %d: unterminated single quote" % line)
            out.append(src[j + 1:k])
            j = k + 1
            continue
        if c == '"':
            end = _skip_dquote(src, j)
            inner = src[j + 1:end - 1]
            if "$" in inner or "`" in inner:
                dynamic = True
            out.append(re.sub(r'\\([$`"\\\n])', r"\1", inner))
            j = end
            continue
        if c == "$" and j + 1 < n and (src[j + 1] in "({" or NAME_RE.match(src[j + 1]) or src[j + 1] in "@*#?$!-0123456789"):
            dynamic = True
            if src.startswith("$((", j):
                j = _match_close(src, j, "$((")
            elif src.startswith("$(", j) or src.startswith("${", j):
                j = _match_close(src, j, src[j:j + 2])
            elif NAME_RE.match(src[j + 1]):
                j += 2
                while j < n and (src[j].isalnum() or src[j] == "_"):
                    j += 1
            else:
                j += 2  # a special parameter: $1, $?, $@, ...
            continue
        if c == "`":
            dynamic = True
            j = _match_close(src, j, "`")
            continue
        out.append(c)
        j += 1
    if j == start:
        return None, i, line, 0
    text = src[start:j]
    return Tok("WORD", text, line, None if dynamic else "".join(out), dynamic), j, line, text.count("\n")


def _assignment(tok):
    if tok.kind != "WORD" or "=" not in tok.text:
        return False
    return bool(NAME_RE.match(tok.text.split("=", 1)[0]))


def walk(toks):
    """The command words a token stream runs: ([(word Tok, how)], {function names}, [(line, inner script)])."""
    calls, functions, nested = [], set(), []
    case_stack = []
    at_start = True
    forced, piped = None, False
    i, n = 0, len(toks)

    def next_word(k):
        while k < n:
            t = toks[k]
            if t.kind == "IONUM":
                k += 1
                continue
            if t.kind == "OP" and t.text in REDIRECTIONS:
                k += 2
                continue
            return k
        return k

    def ends_command(t):
        return t.kind == "NL" or (t.kind == "OP" and (t.text in SEPARATORS or t.text in (")", ";;")))

    def stdin_redirected(k):
        """Whether the simple command around toks[k] redirects its standard input (<, <<, <<-, <>; fd 0 or none)."""
        a = k
        while a > 0 and not ends_command(toks[a - 1]):
            a -= 1
        b = k
        while b < n and not ends_command(toks[b]):
            b += 1
        return any(toks[x].kind == "OP" and toks[x].text in STDIN_REDIRECTIONS
                   and not (x > 0 and toks[x - 1].kind == "IONUM" and toks[x - 1].text != "0") for x in range(a, b))

    def runner_program(k, name):
        """What a runner runs: ("program", index of its program word), ("script", index of a flock -c command
        string), ("none", None) when it runs none, or ("unreadable", why)."""
        with_arg, without, operands, nopgm = RUNNERS[name]
        ended = False
        while True:
            k = next_word(k)
            if k >= n or toks[k].kind != "WORD":
                return "none", None
            lit = toks[k].literal
            if lit is None:
                if ended or operands == 0:
                    return "program", k  # the program word itself is built at run time: refused as such
                return "unreadable", "an argument built at run time where an option or an operand stands"
            if not ended and lit == "--":
                ended, k = True, k + 1
                continue
            if not ended and lit.startswith("-") and (len(lit) > 1 or name == "env"):
                opt = lit.split("=", 1)[0]
                if opt in nopgm:
                    return "none", None
                if name == "flock" and opt in ("-c", "--command"):
                    p = next_word(k + 1)
                    return ("script", p) if p < n and toks[p].kind == "WORD" else ("unreadable", "%s with no command" % lit)
                if opt in with_arg and "=" not in lit:
                    v = next_word(k + 1)
                    if v >= n or toks[v].kind != "WORD":
                        return "unreadable", "the option %s with no value" % lit
                    k = v + 1
                    continue
                if opt in with_arg or opt in without or any(len(s) == 2 and lit.startswith(s) and len(lit) > 2 for s in with_arg):
                    k += 1
                    continue
                return "unreadable", "the option %s, which this gate cannot read" % lit
            if name == "env" and _assignment(toks[k]):
                k += 1
                continue
            if operands:
                operands, k = operands - 1, k + 1
                continue
            return "program", k

    while i < n:
        t = toks[i]
        if case_stack and case_stack[-1] == "pattern":
            if t.kind == "WORD" and t.literal == "esac":
                case_stack.pop()
                at_start = False
            elif t.kind == "OP" and t.text == ")":
                case_stack[-1] = "body"
                at_start = True
            i += 1
            continue
        if t.kind == "NL" or (t.kind == "OP" and t.text in SEPARATORS):
            at_start, forced, piped = True, None, t.kind == "OP" and t.text == "|"
            i += 1
            continue
        if t.kind == "OP" and t.text == ")":
            at_start, forced = False, None
            i += 1
            continue
        if t.kind == "OP" and t.text == ";;":
            if case_stack:
                case_stack[-1] = "pattern"
            at_start = False
            i += 1
            continue
        if t.kind == "OP" and t.text in REDIRECTIONS:
            i += 2
            continue
        if t.kind == "IONUM" or t.kind == "OP":
            i += 1
            continue
        # a WORD
        if not at_start:
            i += 1
            continue
        # a word exec, a runner or busybox hands on is the program it runs: never an assignment, a keyword or a function
        how, forced = forced or "call", None
        kw = how == "call"
        if kw and _assignment(t):
            i += 1
            continue
        w = t.literal
        if kw and w in START_KEYWORDS:
            i += 1
            continue
        if kw and w in END_KEYWORDS:
            at_start = False
            i += 1
            continue
        if kw and w == "esac" and case_stack:
            case_stack.pop()
            at_start = False
            i += 1
            continue
        if kw and w == "for":
            i += 1
            while i < n and not (toks[i].kind == "NL" or (toks[i].kind == "OP" and toks[i].text == ";")):
                i += 1
            continue
        if kw and w == "case":
            i += 1
            while i < n and not (toks[i].kind == "WORD" and toks[i].literal == "in"):
                i += 1
            case_stack.append("pattern")
            i += 1
            continue
        if (kw and i + 2 < n and toks[i + 1].kind == "OP" and toks[i + 1].text == "("
                and toks[i + 2].kind == "OP" and toks[i + 2].text == ")" and w is not None and NAME_RE.match(w)):
            functions.add(w)
            i += 3
            at_start = True
            continue
        calls.append((t, how))
        at_start = False
        here = i
        i += 1
        base = os.path.basename(w) if w else None
        if kw and w == "exec":
            at_start, forced = True, "program"  # exec replaces the shell with a program; a builtin is not one
        elif kw and w == "command":
            k = next_word(i)
            if k < n and toks[k].kind == "WORD" and toks[k].literal and toks[k].literal.startswith("-"):
                if set(toks[k].literal[1:]) & {"v", "V"}:
                    p = next_word(k + 1)
                    if p < n and toks[p].kind == "WORD":
                        calls.append((toks[p], "probe"))
                    i = p + 1
                    continue
                i = k + 1
            at_start = True
        elif kw and w == "trap":
            k = next_word(i)
            if k < n and toks[k].kind == "WORD" and toks[k].literal == "--":
                k = next_word(k + 1)
            if k < n and toks[k].kind == "WORD":
                if toks[k].literal is None:  # an action built at run time runs code the gate cannot see
                    calls.append((toks[k], "dynamic-script"))
                elif not toks[k].literal.startswith("-"):
                    nested.append((toks[k].line, toks[k].literal))
        elif base in SHELLS:
            # the shell's options (clusters such as -ec; -o takes a name), then -c's command string, else a script
            # file, else commands read from its standard input: only a shell left on the console's input is read as
            # running nothing the gate cannot see
            k, cmode, smode = next_word(i), False, False
            while k < n and toks[k].kind == "WORD" and toks[k].literal is not None:
                lit = toks[k].literal
                if lit == "--":
                    k = next_word(k + 1)
                    break
                if len(lit) < 2 or lit[0] not in "-+":
                    break
                cmode, smode = cmode or "c" in lit[1:], smode or "s" in lit[1:]
                k = next_word(next_word(k + 1) + 1) if "o" in lit[1:] else next_word(k + 1)
            arg = toks[k] if k < n and toks[k].kind == "WORD" else None
            if cmode:
                if arg is None:
                    calls.append((t, "unreadable:-c with no command string"))
                elif arg.literal is None:
                    calls.append((arg, "dynamic-script"))
                else:
                    nested.append((arg.line, arg.literal))
            elif arg is not None and not smode:
                calls.append((t, "unreadable:it runs the script file %s, which this gate does not read" % arg.text))
            elif piped or stdin_redirected(here):
                calls.append((t, "unreadable:it runs the commands on its standard input (a pipe, a file or a "
                                 "here-document), which this gate does not read"))
        elif base == "busybox":
            k = next_word(i)
            if k < n and toks[k].kind == "WORD":
                if toks[k].literal is None:
                    calls.append((toks[k], "dynamic-script"))
                elif not toks[k].literal.startswith("-"):  # busybox APPLET runs that applet, link or not
                    i, at_start, forced = k, True, "applet"
        elif base in RUNNERS:
            kind, k = runner_program(i, base)
            if kind == "program":
                i, at_start, forced = k, True, "program"
            elif kind == "script":  # flock -c: a command string the shell runs
                if toks[k].literal is None:
                    calls.append((toks[k], "dynamic-script"))
                else:
                    nested.append((toks[k].line, toks[k].literal))
            elif kind == "unreadable":
                calls.append((t, "unreadable:" + k))
            elif base in RUNNERS_WITH_A_DEFAULT:
                calls.append((t, "unreadable:with no program named it runs one of its own choosing"))
    return calls, functions, nested


def enumerate_commands(src, line0=1, depth=0):
    """(calls, functions): every command word the script can run, nested code included."""
    if depth > 20:
        raise ParseError("command substitutions nested deeper than 20")
    toks, subs = tokenize(src, line0)
    calls, functions, nested = walk(toks)
    for line, inner in subs + nested:
        c2, f2 = enumerate_commands(inner, line, depth + 1)
        calls.extend(c2)
        functions |= f2
    return calls, functions


# ---- the initramfs, as a directory or as an archive --------------------------------------------------------

class DirTree:
    def __init__(self, root):
        self.root = Path(root)
        self.label = str(root)

    def lookup(self, rel):
        """(kind, target) for an archive-relative path: kind file/link/dir/other, or (None, None) when absent."""
        p = self.root / rel
        try:
            st = os.lstat(p)
        except OSError:
            return None, None
        if stat.S_ISLNK(st.st_mode):
            return "link", os.readlink(p)
        if stat.S_ISREG(st.st_mode):
            return "file", st.st_mode
        if stat.S_ISDIR(st.st_mode):
            return "dir", None
        return "other", None

    def path_on_disk(self, rel):
        return self.root / rel

    def read_init(self):
        return (self.root / "init").read_text(encoding="utf-8", errors="surrogateescape")


class CpioTree:
    def __init__(self, path):
        raw = Path(path).read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.GzipFile(fileobj=__import__("io").BytesIO(raw)).read()
        self.entries, self.label = {}, str(path)
        off = 0
        while True:
            if raw[off:off + 6] not in (b"070701", b"070702"):
                if not self.entries:
                    raise ParseError("%s: not a newc cpio archive (the first archive of an initrd may be a microcode "
                                     "archive: extract the main one first)" % path)
                raise ParseError("%s: bad cpio header at offset %d" % (path, off))
            hdr = raw[off:off + 110]
            field = [int(hdr[6 + 8 * k:14 + 8 * k], 16) for k in range(13)]
            mode, filesize, namesize = field[1], field[6], field[11]
            name_start = off + 110
            name = raw[name_start:name_start + namesize - 1].decode("utf-8", "surrogateescape")
            data_start = (name_start + namesize + 3) & ~3
            data = raw[data_start:data_start + filesize]
            off = (data_start + filesize + 3) & ~3
            if name == "TRAILER!!!":
                break
            rel = name[2:] if name.startswith("./") else name.lstrip("/")
            if rel in ("", "."):
                continue
            self.entries[rel] = (mode, data)

    def lookup(self, rel):
        e = self.entries.get(rel)
        if e is None:
            return None, None
        mode, data = e
        if stat.S_ISLNK(mode):
            return "link", data.decode("utf-8", "surrogateescape")
        if stat.S_ISREG(mode):
            return "file", mode
        if stat.S_ISDIR(mode):
            return "dir", None
        return "other", None

    def data(self, rel):
        return self.entries[rel][1]

    def read_init(self):
        kind, _ = self.lookup("init")
        if kind != "file":
            raise ParseError("%s holds no /init file" % self.label)
        return self.data("init").decode("utf-8", "surrogateescape")


def resolve(tree, rel, hops=0):
    """Follow links inside the archive: (final archive path, its mode) for a regular file, else (None, reason)."""
    if hops > 40:
        return None, "too many levels of links"
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    cur = []
    for idx, part in enumerate(parts):
        if part == "..":
            if cur:
                cur.pop()
            continue
        cand = "/".join(cur + [part])
        kind, target = tree.lookup(cand)
        if kind is None:
            return None, "%s is absent" % cand
        if kind == "link":
            rest = parts[idx + 1:]
            base = [] if target.startswith("/") else cur
            return resolve(tree, "/".join(base + [p for p in target.split("/") if p] + rest), hops + 1)
        if kind == "file" and idx < len(parts) - 1:
            return None, "%s is a file, not a directory" % cand
        cur.append(part)
    final = "/".join(cur)
    kind, mode = tree.lookup(final)
    if kind != "file":
        return None, "%s is a %s, not a file" % (final, kind or "missing entry")
    if not mode & 0o111:
        return None, "%s is not executable" % final
    return final, mode


class Busybox:
    """The archive's busybox, run to list its applets and to ask its shell what is built in."""

    def __init__(self, tree, final_path):
        self.tree, self.path, self._tmp, self.applets = tree, final_path, None, None
        if isinstance(tree, DirTree):
            self.exe = str(tree.path_on_disk(final_path))
        else:
            self._tmp = tempfile.TemporaryDirectory(prefix="initramfs-commands-")
            self.exe = os.path.join(self._tmp.name, "busybox")
            with open(self.exe, "wb") as fh:
                fh.write(tree.data(final_path))
            os.chmod(self.exe, 0o700)
        r = self._run([self.exe, "--list"])
        if r.returncode != 0 or not r.stdout.strip():
            raise ParseError("the archive's busybox (%s) did not list its applets (rc %d): %s"
                             % (final_path, r.returncode, r.stderr.strip()[:200]))
        self.applets = set(r.stdout.split())

    @staticmethod
    def _run(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, env={"PATH": "/nonexistent"})

    def builtins(self, names):
        """The subset of names the busybox shell reports as builtins (asked with no PATH, so no program can answer)."""
        if not names:
            return set()
        script = "".join("type %s\n" % _sh_quote(nm) for nm in sorted(names))
        r = self._run([self.exe, "sh", "-c", script])
        found = set()
        for ln in r.stdout.splitlines():
            m = re.match(r"^(\S+) is a (?:special )?shell builtin$", ln)
            if m and m.group(1) in names:
                found.add(m.group(1))
        return found

    def close(self):
        if self._tmp is not None:
            self._tmp.cleanup()


def _sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def find_program(tree, word):
    """(archive path the word runs, how) or (None, why)."""
    if "/" in word:
        final, why = resolve(tree, word.lstrip("/"))
        return (final, why) if final else (None, why)
    misses = []
    for d in SEARCH_DIRS:
        final, why = resolve(tree, "%s/%s" % (d, word))
        if final:
            return final, "%s/%s" % (d, word)
        misses.append(why)
    return None, "no executable in %s" % ", ".join(d + "/" for d in SEARCH_DIRS)


def check(init_path, tree, optional):
    """(head line, report lines, refusals, absent optional programs) for the script init_path (None: the archive's /init)."""
    if init_path is None:
        src, init_path = tree.read_init(), "/init"
    else:
        src = Path(init_path).read_text(encoding="utf-8", errors="surrogateescape")
    calls, functions = enumerate_commands(src)
    lines, probes, dynamic, progs, applets, unread = {}, {}, [], {}, {}, []
    for tok, how in calls:
        if how.startswith("unreadable:"):
            unread.append((tok, how.split(":", 1)[1]))
            continue
        if how == "dynamic-script" or tok.literal is None:
            dynamic.append(tok)
            continue
        if how == "probe":
            probes.setdefault(tok.literal, []).append(tok.line)
            continue
        if how == "applet":  # busybox APPLET: the applet must be in the archive's busybox
            applets.setdefault(tok.literal, []).append(tok.line)
            continue
        if how == "program":  # run by exec or a runner: an executable file, never a builtin or a function
            progs.setdefault(tok.literal, []).append(tok.line)
            continue
        if tok.literal in functions:
            continue
        lines.setdefault(tok.literal, []).append(tok.line)
    for w in DYNAMIC_BUILTINS & set(lines):
        dynamic.extend(Tok("WORD", w, ln, w) for ln in lines.pop(w))

    report, refused = [], []
    shebang = src.splitlines()[0] if src.startswith("#!") else ""
    interp = shebang[2:].strip().split()[0] if shebang else None
    bb_final = None
    for cand in [interp] if interp else []:
        final, why = find_program(tree, cand)
        if final is None:
            refused.append("%s (the interpreter line: %s)" % (cand, why))
        else:
            report.append("  interpreter %-14s -> %s" % (cand, final))
            if os.path.basename(final) == "busybox":
                bb_final = final
    if bb_final is None:
        final, _ = resolve(tree, "bin/busybox")
        bb_final = final
    busybox = Busybox(tree, bb_final) if bb_final else None
    if interp and busybox and os.path.basename(interp) not in busybox.applets:
        refused.append("%s (the interpreter line: it links to busybox, which has no %s applet)"
                       % (interp, os.path.basename(interp)))
    try:
        candidates = {w for w in lines if w in BUILTIN_CANDIDATES}
        confirmed = busybox.builtins(candidates) if busybox else set()
        n_builtin = n_program = 0
        absent_optional = []
        for w in sorted(lines, key=lambda x: (lines[x][0], x)):
            at = "line" + ("s " if len(lines[w]) > 1 else " ") + ", ".join(str(x) for x in sorted(set(lines[w])))
            if w in confirmed:
                n_builtin += 1
                report.append("  builtin     %-14s (the archive's own shell)                 %s" % (w, at))
                continue
            final, how = find_program(tree, w)
            if final and os.path.basename(final) == "busybox" and busybox:
                name = os.path.basename(w)
                if name != "busybox" and name not in busybox.applets:
                    final, how = None, "%s links to busybox, which has no %s applet" % (how, name)
            if final:
                n_program += 1
                report.append("  program     %-14s -> %-40s %s" % (w, final + (" (busybox applet)" if os.path.basename(final) == "busybox" else ""), at))
            elif w in optional:
                absent_optional.append(w)
                report.append("  OPTIONAL    %-14s absent: %s (declared optional)    %s" % (w, how, at))
            else:
                refused.append("%s (%s; %s)" % (w, how, at))
                report.append("  MISSING     %-14s %s    %s" % (w, how, at))
        for w in sorted(progs, key=lambda x: (progs[x][0], x)):
            at = "line" + ("s " if len(progs[w]) > 1 else " ") + ", ".join(str(x) for x in sorted(set(progs[w])))
            final, how = find_program(tree, w)
            if final and os.path.basename(final) == "busybox" and busybox:
                name = os.path.basename(w)
                if name != "busybox" and name not in busybox.applets:
                    final, how = None, "%s links to busybox, which has no %s applet" % (how, name)
            if final:
                n_program += 1
                report.append("  runs        %-14s -> %-40s %s (a program exec or a runner starts)" % (w, final + (" (busybox applet)" if os.path.basename(final) == "busybox" else ""), at))
            elif w in optional:
                absent_optional.append(w)
                report.append("  OPTIONAL    %-14s absent: %s (declared optional)    %s" % (w, how, at))
            else:
                refused.append("%s (%s, and exec or a runner needs an executable file here, a shell builtin is not one; %s)" % (w, how, at))
                report.append("  MISSING     %-14s %s (run by exec or a runner)    %s" % (w, how, at))
        for w in sorted(applets, key=lambda x: (applets[x][0], x)):
            at = "line" + ("s " if len(applets[w]) > 1 else " ") + ", ".join(str(x) for x in sorted(set(applets[w])))
            if busybox and w in busybox.applets:
                n_program += 1
                report.append("  applet      %-14s (busybox %s)                       %s" % (w, w, at))
            else:
                why = "the archive's busybox has no %s applet" % w if busybox else "the archive holds no busybox"
                refused.append("%s (%s; %s)" % (w, why, at))
                report.append("  MISSING     %-14s %s    %s" % (w, why, at))
        for tok, why in unread:
            refused.append("%s (line %d: %s)" % (tok.text, tok.line, why))
            report.append("  UNREADABLE  %-14s line %d: %s" % (tok.text, tok.line, why))
        for tok in dynamic:
            refused.append("%s (line %d: a command built at run time, which this gate cannot resolve)" % (tok.text, tok.line))
            report.append("  DYNAMIC     %-14s line %d" % (tok.text, tok.line))
    finally:
        if busybox:
            busybox.close()
    head = ("[%s] %s in %s: %d command word(s) — %d shell builtin(s) confirmed by the archive's shell, %d program(s); "
            "functions defined: %s; probed with command -v: %s"
            % (PROG, init_path, tree.label, len(lines) + len(progs) + len(applets), n_builtin, n_program, ", ".join(sorted(functions)) or "none",
               ", ".join(sorted(probes)) or "none"))
    return head, report, refused, absent_optional


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check-initramfs-commands.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--init", help="the init script to check (default: the archive's own /init; required with --list)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--root", help="a staged initramfs work tree")
    g.add_argument("--cpio", help="a built initramfs archive (newc, optionally gzip-compressed)")
    g.add_argument("--list", action="store_true", help="print the script's command words and stop")
    ap.add_argument("--optional", action="append", default=[], metavar="NAME",
                    help="a program the script probes for and the builder bundles only when present")
    a = ap.parse_args(argv)
    if a.list and not a.init:
        ap.error("--list needs --init")
    try:
        if a.list:
            src = Path(a.init).read_text(encoding="utf-8", errors="surrogateescape")
            calls, functions = enumerate_commands(src)
            seen = {}
            for tok, how in calls:
                key = tok.text if tok.literal is None else tok.literal
                if tok.literal in functions and how == "call":
                    continue
                seen.setdefault((key, how), []).append(tok.line)
            for (w, how), lns in sorted(seen.items(), key=lambda kv: (kv[1][0], kv[0])):
                print("%s\t%s\t%s" % (w, how, ",".join(str(x) for x in sorted(set(lns)))))
            print("[%s] %s: %d distinct command word(s); functions defined: %s"
                  % (PROG, a.init, len({w for w, how in seen if how in ("call", "program", "applet")}), ", ".join(sorted(functions)) or "none"))
            return 0
        if a.root and not Path(a.root).is_dir():
            raise ParseError("--root %s is not a directory" % a.root)
        tree = DirTree(a.root) if a.root else CpioTree(a.cpio)
        head, report, refused, absent_optional = check(a.init, tree, set(a.optional))
        a.init = a.init or "/init"
    except (OSError, ParseError, subprocess.SubprocessError) as exc:
        print("[%s] ERROR: %s" % (PROG, exc), file=sys.stderr)
        return 2
    print(head)
    for ln in report:
        print(ln)
    if refused:
        print("[%s] REFUSED: %d command word(s) %s runs cannot run inside %s: %s"
              % (PROG, len(refused), a.init, tree.label, "; ".join(refused)))
        return 1
    print("[%s] PASS: every command word %s runs resolves inside %s%s"
          % (PROG, a.init, tree.label, "" if not absent_optional else
             " (optional and absent, by declaration: %s)" % ", ".join(absent_optional)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
