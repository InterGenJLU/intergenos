# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-6 — per-install dispatch-token key infrastructure (mint + verify primitive).

THE PROBLEM (audit AI-6 + the injection-cluster ledger, 2026-05-29). When a
privileged (root) tool is dispatched, the provenance gate's decision is made in
the *user* daemon context and **never crosses into root**. `privileged_dispatch`
re-checks the allowlist + argument schema, but it implicitly trusts that —
because it was invoked at all — the upstream gate returned `execute`. Any path
that reaches the pkexec runner without a genuine human-approved decision (a
future agentic-loop / XML-context-injection path, or any non-gate bypass) has no
independent backstop at the root crossing.

THE DESIGN (decided 2026-05-29 evening — option iii, BOTH mandatory).
Every privileged action is gated on a **human review-modal approval** (NOT on the
AI's self-declared provenance — the gate is doubly inert today and cannot be
trusted to pick which calls get human review; the privileged tier ALWAYS prompts).
On approval the user daemon **mints a token** that cryptographically binds THAT
human approval to THIS exact call:

    HMAC-SHA256( per-install-key,
                 { tool, args_sha256, approval-nonce, uid, iat, exp } )

The token crosses to root (argv into the pkexec runner, which re-exports it into
its sanitized ENV — see `intergen-privileged-runner`); `privileged_dispatch`
verifies the signature + binding (tool + args + uid) + freshness (exp) and that
the approval-nonce has not been replayed (root-side consumed-nonce store), BEFORE
executing. No valid token → refuse. Root stops *trusting* that the gate fired; it
now *verifies* that a human approved this exact (tool, args, uid).

WHY A FILE KEY (0o600), NOT GNOME KEYRING. The key must be readable by BOTH the
user daemon (mint) AND root (verify). The login keyring may be LOCKED at daemon
autostart, and root reading a per-user-session keyring across the privilege
boundary is fragile. A 0o600 file in ~/.config/intergen mirrors the shipped
`setup.py:_generate_auth_token` web-token precedent (chosen for exactly this
startup-reliability reason) and is transparent + inspectable by the user
(Prime Directive). HMAC-symmetric is sufficient: same machine, same trust domain;
the threat is prompt-INJECTION (which can only make the LLM emit a ToolCall — it
cannot read the key file or call the mint), not RCE.

DIRECT CANONICAL SPELLINGS OF THE INSTALL CREDENTIAL FILES ARE SCREENED AS DEFENCE
IN DEPTH. The dispatcher checks string argument values for the canonical relative
names before execution. This is not a file-identity boundary: alternate path
spellings and paths derived inside a tool are outside what argument inspection can
establish.

WHERE THE BOUNDARY IS. A ToolCall can read any file the daemon's own account can
read, and this key is owned by that account, so "it cannot read the key file"
held only as an intention until something refused the read. Two boundaries
refuse it. For the tools that open a path a caller supplied inside the daemon,
the refusal is further down this module, at the point a file is actually opened:
`open_descriptor_secret_reason` decides from the open descriptor, and those tools
ask it after the open and before any read or write. A tool that runs as its own
process, run_command's child or an MCP server, performs its own opens, which no
check in this package sees, so that process starts as a transient service in
which these files are inaccessible (intergen/tool_process.py): the kernel refuses
every open of them there, whatever spelling or derivation reached them, a
parent-directory spelling, a symbolic link, or a containing directory from which
the process derives the protected child. What neither boundary covers is named
in intergen/tool_process.py: a second name for either file made before the
process boundary existed, which is reported and not refused, and a process
outside the service that a tool's process can still drive some other way.

DIVISION OF LABOR (AI-6 build, branch off master 5ce0dc41):
  * User side (this module + the mint call-site) — key gen-on-first-run, the canonical
    token format, `mint_token`, and the shared `verify_token` crypto primitive.
  * Root side — wire `verify_token` into `privileged_dispatch.main`, add
    the persistent consumed-nonce store, read the key via PKEXEC_USER's home
    (NOT HOME=/root), and the strong run_command classifier (AI-4).
  * Verification — an independent review of both sides.

This module is stdlib-only (hmac, hashlib, secrets, json, base64, shlex, time, os,
errno, stat, pwd, pathlib) so it is auditable in isolation and carries no
third-party trust.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import logging
import os
import pwd
import secrets
import shlex
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from intergen.held_open import HeldOpenRefused, held_directory, open_held_at
from intergen.private_state import private_dir

logger = logging.getLogger(__name__)

# --- Wire-format + policy constants ------------------------------------------

#: Token wire-format version. Bump only on a breaking payload-shape change so a
#: stale daemon + fresh root (or vice versa across an upgrade) fail closed with a
#: clear version error rather than a silent mis-verify.
TOKEN_VERSION = 1

#: The per-install signing key file, relative to the owning user's home. Mirrors
#: the web-token at ~/.config/intergen/web-token (setup.py).
KEY_RELATIVE_PATH = (".config", "intergen", "dispatch-key")

#: The install's OWN secret files, each relative to the owning user's home, with
#: the reason a refusal states. One list, read by both the dispatcher's screen
#: (`secret_file_reason`, on argument text) and the file boundary
#: (`open_descriptor_secret_reason`, on an open descriptor), so there is one
#: answer to what this install's own secrets are. The web-token is here for the
#: same reason the header cites it as the precedent: it is the credential the
#: browser surface authenticates with.
SECRET_RELATIVE_PATHS = {
    KEY_RELATIVE_PATH:
        "the per-install dispatch signing key, which is what lets the root side "
        "tell a human approval from an injected one",
    (".config", "intergen", "web-token"):
        "the browser interface's authentication token",
}

#: 256-bit key, hex-encoded (64 chars) — matches the web-token's token_hex(32).
KEY_BYTES = 32

#: Required file mode for the key (owner read/write only). Enforced on read so a
#: world/group-readable key (tamper or mis-install) fails closed.
KEY_MODE = 0o600

#: Short token lifetime. The human approved in the review modal immediately
#: before the mint; the only variable-latency step before root-side verify is the
#: PolicyKit password prompt (often cached via auth_admin_keep). 120s covers a
#: slow password entry while keeping the replay window tight. The single-use
#: approval-nonce (root consumed-nonce store) is the primary replay defense; exp
#: is the secondary bound.
DEFAULT_TTL_SECONDS = 120

# The token used to travel to the root side in an environment variable, which
# the runner re-exported from its own argv. That constant is gone as of
# 2026-08-24: the token now travels with the tool name and the arguments inside
# an owner-only request file (intergen.privileged_request), and the request
# file's path is the only thing on any command line. Naming an environment
# channel nothing reads would be a claim without a mechanism behind it.


# --- Failure taxonomy --------------------------------------------------------

class DispatchTokenError(Exception):
    """Base class for all mint/verify failures. Callers fail CLOSED on any
    subclass — an unverifiable token is a refused dispatch, never a fallback to
    'trust the caller'."""


class KeyError_(DispatchTokenError):
    """Signing key missing, wrong permissions, or malformed."""


class MalformedToken(DispatchTokenError):
    """Token string does not parse as version.body.mac."""


class BadSignature(DispatchTokenError):
    """HMAC mismatch — the token was not minted by this install's key, or was
    tampered with in transit."""


class BindingMismatch(DispatchTokenError):
    """Signature is valid but the token does not bind to THIS (tool, args, uid)
    — a replay against a different call, or a forwarded token."""


class TokenExpired(DispatchTokenError):
    """Signature + binding valid but the token is past its exp (or not yet
    valid). Distinct from BindingMismatch so the root side can log the precise
    refusal cause."""


# --- Canonicalization --------------------------------------------------------

def _canonical_json(obj: object) -> str:
    """Deterministic JSON: sorted keys, no whitespace, ASCII-escaped. Both mint
    and verify (and the args digest) MUST agree byte-for-byte, so this is the one
    serialization used everywhere in this module."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def args_digest(args: dict) -> str:
    """SHA-256 (hex) of the canonical-JSON form of a tool's argument object.

    Binding to the args *digest* rather than the raw args keeps the token small
    and fixed-size while still pinning the exact argument set the human approved:
    root recomputes this digest over the args it received and compares.
    """
    return hashlib.sha256(_canonical_json(args).encode("utf-8")).hexdigest()


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64 without padding (padding is '=' which is harmless but we
    strip it for a cleaner single-token-no-special-chars wire form)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# --- Key management ----------------------------------------------------------

def dispatch_key_path(home: Path | None = None) -> Path:
    """Path to the signing key for the current user (mint side).

    home overrides the base (testing). Defaults to the real home — NOT
    Path.home(), which is HOME-env-driven; mint always runs as the user with a
    correct HOME, but we resolve via the password database for robustness against
    a stripped/forged HOME in the daemon's environment.
    """
    if home is None:
        home = _owning_home()
    return home.joinpath(*KEY_RELATIVE_PATH)


def _owning_home() -> Path:
    """The home directory of the account that owns this install's files.

    Resolved through the password database rather than HOME, for the reason
    dispatch_key_path above states: a stripped or forged HOME in the daemon's
    environment must not be able to move where the install's own files are
    believed to live. One function, so the mint side and the file boundary below
    cannot come to different answers.

    Under the privileged runner the process runs as root, and root's home is not
    where the install keeps its files. An approved privileged tool runs there,
    and the runner reports the account that asked for it in PKEXEC_UID, so that
    account's home is the answer, as dispatch_key_path_for_user already gives
    the verify side. PKEXEC_UID is believed only in a process that runs as root,
    where the runner is what set it; in any other process it is ordinary
    environment, and believing it would let a caller move the answer. A value
    that is not a number raises instead of falling back to root's own home,
    because that fallback is exactly the wrong account.
    """
    uid = os.getuid()
    if uid == 0 and "PKEXEC_UID" in os.environ:
        uid = int(os.environ["PKEXEC_UID"])
    return Path(pwd.getpwuid(uid).pw_dir)


def expanduser_for_install_owner(text: str) -> Path:
    """`Path(text).expanduser()`, with a bare ~ read as the install owner's home.

    A tool path written as ~ or ~/... means the home of the person who asked for
    it. expanduser answers from HOME, and the privileged runner sets HOME=/root,
    so in the root context an approved "~/.bashrc" would be written in root's
    home instead of the one the person approved. Under the runner a bare ~ is
    therefore read from `_owning_home`; everywhere else expanduser already
    answers for the same account and its answer is kept. A ~name spelling names
    its account outright and is left to expanduser.

    Raises InstallOwnerUnresolved under the runner when the owner cannot be
    established, rather than guessing a home.
    """
    if (os.getuid() == 0 and "PKEXEC_UID" in os.environ
            and (text == "~" or text.startswith("~/"))):
        try:
            home = str(_owning_home()).rstrip("/")
        except (KeyError, ValueError) as exc:
            raise InstallOwnerUnresolved(str(exc)) from exc
        # Joined as text, the way expanduser joins, so "~//etc" stays under the
        # home instead of becoming an absolute path of its own.
        return Path((home + text[1:]) or "/")
    return Path(text).expanduser()


class LinkOnTheRequestedPath(OSError):
    """Under the privileged runner, the requested path passes through a link that
    root does not own.

    The person approved the write by its arguments, so the object written must
    be the one they name, and a link an account other than root can make or
    change would decide which object that is. A link root owns that is replaced
    while it is read counts as one root does not own. Nothing is read or
    written. The message names the link and where it leads, so the person can
    ask for that path by its own name.
    """


def resolved_for_a_write(path: object) -> Path:
    """The requested path of a write, resolved as Path.resolve() resolves it,
    except that under the privileged runner no link root does not own is
    followed.

    Outside the runner this is Path(path).resolve(). Under the runner the path,
    made absolute as resolve() makes it, is read one component at a time from
    /, each component opened without being followed (O_PATH|O_NOFOLLOW) and
    its type and owner read from that descriptor: a component that is not a
    link is taken as it is; one that does not exist, or lies below one that is
    not a directory, is joined as resolve() joins it; a link root owns, as the
    system layout's own links are, is followed, its target read through the
    same descriptor, so the link whose owner was accepted is the link whose
    target is read; and any other link raises LinkOnTheRequestedPath before
    anything is read or written. A link root owns whose name no longer names
    that same link once its target has been read was replaced while it was
    read, and raises LinkOnTheRequestedPath the same way. The result is the
    path every later check is asked of and the file boundary opens
    (_opened_as_checked). More than 40 links followed raise OSError(ELOOP);
    any other failure to read a component is raised as it is.
    """
    if not _under_the_privileged_runner():
        return Path(path).resolve()
    text = os.fspath(path)
    if not os.path.isabs(text):
        text = os.path.join(os.getcwd(), text)
    pending = [part for part in text.split(os.sep) if part]
    pending.reverse()
    resolved = "/"
    followed = 0
    while pending:
        part = pending.pop()
        if part == ".":
            continue
        if part == "..":
            resolved = os.path.dirname(resolved)
            continue
        here = os.path.join(resolved, part)
        try:
            fd = os.open(here, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        except (FileNotFoundError, NotADirectoryError):
            resolved = here
            continue
        try:
            found = os.fstat(fd)
            if not stat.S_ISLNK(found.st_mode):
                resolved = here
                continue
            target = os.readlink("", dir_fd=fd)
            if found.st_uid != 0:
                raise LinkOnTheRequestedPath(
                    f"Refused: {here} is a link to {target}, and it is not "
                    f"root's: under the privileged runner a write goes only to "
                    f"the object its request names, never through a link an "
                    f"account other than root can make or change. The "
                    f"requested path leads to {os.path.realpath(text)}; ask "
                    f"for that path by its own name to write there. Nothing "
                    f"was read or written.")
            try:
                now = os.lstat(here)
                same = (now.st_dev, now.st_ino) == (found.st_dev, found.st_ino)
            except (FileNotFoundError, NotADirectoryError):
                same = False
            if not same:
                raise LinkOnTheRequestedPath(
                    f"Refused: {here} was a link root owns, to {target}, and "
                    f"was replaced while it was read, so it is not a link "
                    f"root owns: under the privileged runner a write goes only "
                    f"to the object its request names, never through a link "
                    f"an account other than root can make or change. The "
                    f"requested path now leads to {os.path.realpath(text)}; "
                    f"ask for that path by its own name to write there. "
                    f"Nothing was read or written.")
        finally:
            os.close(fd)
        followed += 1
        if followed > 40:
            raise OSError(errno.ELOOP,
                          "too many links followed resolving the requested path",
                          text)
        if os.path.isabs(target):
            resolved = "/"
        pending.extend(reversed([part for part in target.split(os.sep) if part]))
    return Path(resolved)


def secret_file_reason(candidate: object) -> str | None:
    """Why this value, TAKEN WHOLE, is a path to one of the install's own secret
    files, or None.

    The dispatcher's screen, and a screen only. It can reject a canonical textual
    reference before review or execution; it cannot establish which file a tool
    will open, and the file boundary further down this module is what does that.

    The question asked is whether the value IS the file: the final components of
    the path it spells are compared with the components the install keeps its
    credentials under. That is deliberately narrow in two directions a wider
    comparison got wrong. A longer filename that merely begins with one of these
    names is a different file and is not refused — refusing it stopped an ordinary
    read for no gain. And descriptive text that mentions one of these names opens
    nothing, so it is not refused either; a string a tool will hand to a process
    is a command, which `secret_file_named_in_command` handles, and every other
    string is prose.

    Matching is on the path's TAIL, not against a resolved home, for three
    reasons. The daemon may run for any account; a test or a staged install puts
    the same files under a different base; and a path arriving from a model is not
    trustworthy enough to resolve against one expected home and dismiss when it
    does not match.

    `candidate` is whatever a tool argument held, so it is deliberately typed
    loosely: a value that is not a string or a path cannot name a file and
    returns None rather than raising inside the dispatch path.
    """
    if isinstance(candidate, Path):
        text = str(candidate)
    elif isinstance(candidate, str):
        text = candidate
    else:
        return None
    if not text:
        return None

    # expanduser covers the tilde form a person or a model writes; no resolve(),
    # which would touch the filesystem for a value that may be hostile and may
    # not exist. Resolution belongs to the consumer, after its open.
    try:
        parts = Path(text).expanduser().parts
    except (ValueError, RuntimeError):
        return None
    for relative, reason in SECRET_RELATIVE_PATHS.items():
        if len(parts) >= len(relative) and tuple(parts[-len(relative):]) == relative:
            return reason
    return None


def secret_file_named_in_command(text: object) -> str | None:
    """Why a command line names one of the install's own secret files, or None.

    A command is the one argument shape where one of these paths can sit inside a
    longer string and still be opened, because what opens it is a process this
    package does not instrument at the point of opening. The file boundary below
    never sees that open, so for a command the text is all there is.

    The command is split into words the way its runner splits it, and each word is
    asked the same question a path argument is asked. Splitting is what keeps the
    answer honest in both directions: a word that merely begins with one of these
    names is a different file and matches nothing, while a credential path sitting
    anywhere among the words is found wherever it sits.

    This is applied ONLY to a parameter a tool declares as a command line. A
    string a tool will never hand to a process is prose, and refusing prose stops
    the assistant explaining its own security while protecting nothing.
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        words = shlex.split(text)
    except ValueError:
        # An unbalanced quote is not a reason to stop looking: fall back to the
        # plain split, which can only find more words, never fewer.
        words = text.split()
    for word in words:
        reason = secret_file_reason(word)
        if reason is not None:
            return reason
    return None


# --- The file boundary: deciding on the object, not on the name --------------
#
# `secret_file_reason` above reads argument text, which is all a dispatcher has.
# What returns a file is an OPEN, and an open resolves parent components, follows
# symbolic links, and accepts any one of the names a single file may have. A text
# comparison made before that open therefore answers a different question from
# the one that matters, in both directions: it misses a spelling it has not been
# shown, and it refuses a string that was never going to open anything.
#
# What follows decides on the object a consumer actually opened. The decision is
# taken from the open descriptor, so there is no interval between resolving a
# name and using it in which the name can be made to mean something else.


class InstallSecretRefused(Exception):
    """What was opened IS one of the install's own secret files.

    Carries the reason a refusal states, so a consumer hands the person the same
    sentence the dispatcher's own screen would have used.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InstallOwnerUnresolved(Exception):
    """The account that owns this install could not be established.

    Without it the identity leg below has no secret files to compare against,
    and an empty answer would read as "nothing here is a secret". The boundary
    therefore treats this as a reason to refuse, never as an empty set.
    """


class SecretIdentityUnreadable(InstallOwnerUnresolved):
    """A secret file's identity could not be read, and the file is not absent.

    Only a path the lookup reports as not existing contributes nothing to the
    identity leg. Any other failure to stat one (a directory on its path that
    cannot be searched, a path that runs through a file, a loop of links, an I/O
    error) leaves the boundary unable to tell that file from a second name for
    it, so the boundary refuses instead of comparing against a set that is
    missing it. A subclass of InstallOwnerUnresolved, so a caller that refuses on
    an unresolved owner refuses on this too.
    """


def opened_file_refusal_text(reason: str) -> str:
    """The sentence a consumer returns when it refuses one of these files.

    It lives here, beside the reasons themselves, so the three consumers cannot
    drift into three different explanations of the same refusal.
    """
    return (
        f"Refused: the file this path opens is {reason}. The assistant does not "
        "read or replace its own credential files, and no approval can "
        "authorise it to."
    )


def canonical_path_secret_reason(candidate: object) -> str | None:
    """Why an ALREADY-CANONICAL path is one of the install's secret files, or None.

    The tail comparison of `secret_file_reason`, with its text-matching half
    dropped. By the time this is asked the path came from the kernel for an
    object that is already open, so there is nothing hostile left to interpret:
    the only question is whether the final components are the ones the install
    keeps its credentials under, and a longer filename that merely begins with
    one of those names is a different file and stays readable.

    The comparison is on the tail rather than against one resolved home for the
    reasons `secret_file_reason` gives: the daemon may run for any account, and a
    staged install or a test puts the same files under another base.
    """
    if isinstance(candidate, Path):
        text = str(candidate)
    elif isinstance(candidate, str):
        text = candidate
    else:
        return None
    if not text:
        return None
    try:
        parts = Path(text).parts
    except (ValueError, RuntimeError):
        return None
    for relative, reason in SECRET_RELATIVE_PATHS.items():
        if len(parts) >= len(relative) and tuple(parts[-len(relative):]) == relative:
            return reason
    return None


def install_secret_paths() -> tuple[Path, ...]:
    """This install's secret files, as paths under the owning account's home.

    The paths the tool-process boundary (intergen/tool_process.py) makes
    inaccessible to a tool's process. An owner that cannot be resolved raises
    InstallOwnerUnresolved, because a boundary that masked some other account's
    files would look exactly like one that masked these.
    """
    try:
        home = _owning_home()
    except (KeyError, ValueError, OSError) as exc:
        raise InstallOwnerUnresolved(str(exc)) from exc
    return tuple(home.joinpath(*relative) for relative in SECRET_RELATIVE_PATHS)


#: Every secret identity this process has established, per owning home. It only grows:
#: see secret_file_identities.
_SEEN_SECRET_IDENTITIES: dict[Path, dict[tuple[int, int], str]] = {}


def secret_file_identities() -> dict[tuple[int, int], str]:
    """The (device, inode) identity of each of this install's secret files.

    The leg a name cannot launder. A hard link gives one file a second name whose
    own canonical path says nothing about what it points at, so a comparison on
    that name finds nothing to refuse; the identity of the object is the same
    either way.

    This leg speaks only for the files of THIS install, since it has to resolve
    them to stat them; the tail comparison above is what covers a staged install
    or a test under another base. A canonical path the lookup reports as not
    existing (FileNotFoundError: the file, or a directory on its path, is not
    there) adds nothing: an install before its first run has no key to protect
    yet.

    THE SET ONLY GROWS. Every identity this process has read stays in what this
    returns for that owning home, so a credential later moved away from its
    canonical path, by renaming a directory on it, is still refused by identity.
    The daemon reads the identities once at start
    (remember_secret_identities_at_startup), before any tool can run. What this
    does not cover, named rather than claimed: a credential moved before the first
    read in a process is a file this process never saw, and contributes nothing;
    and an inode number the filesystem gives another file after a credential was
    deleted is refused as that credential until the process restarts.

    Any other failure to stat a secret file, a path that runs through a file
    included, raises SecretIdentityUnreadable, and an owner that cannot be
    resolved raises InstallOwnerUnresolved, of which SecretIdentityUnreadable is a
    subclass. Either way an empty entry in its place would certify a second name
    for that file as safe. Each identity is kept the moment it is read, so a
    failure on one credential never costs the identities already read before it.
    """
    try:
        home = _owning_home()
    except (KeyError, ValueError, OSError) as exc:
        raise InstallOwnerUnresolved(str(exc)) from exc
    seen = _SEEN_SECRET_IDENTITIES.setdefault(home, {})
    for relative, reason in SECRET_RELATIVE_PATHS.items():
        path = home.joinpath(*relative)
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SecretIdentityUnreadable(f"{path}: {exc.strerror or exc}") from exc
        seen[(st.st_dev, st.st_ino)] = reason
    return dict(seen)


def remember_secret_identities_at_startup() -> None:
    """Read the install's secret identities once, before any tool can run.

    The daemon calls this at start so the set secret_file_identities keeps is
    populated before a tool could move a credential away from its canonical path.
    A failure is logged and not raised: the file boundary refuses at the open
    whenever an identity cannot be established, so nothing depends on this read.
    """
    try:
        secret_file_identities()
    except (InstallOwnerUnresolved, SecretIdentityUnreadable) as exc:
        logger.warning("the install's secret file identities could not be read at "
                       "start (%s); the file boundary refuses until they can be", exc)


def open_descriptor_secret_reason(fd: int) -> str | None:
    """Why the object behind this OPEN DESCRIPTOR is one of the install's secrets.

    Both legs are asked of the descriptor and never of the caller's string, which
    is what removes the window a check-then-open sequence leaves open: between a
    name being resolved and that name being opened, the name can be made to point
    elsewhere, and a descriptor cannot.

    Fails CLOSED, and says what it lacked. If neither the kernel's own path for
    the object nor the object's identity can be read, the refusal stands with
    that stated: a consumer that cannot establish what it is holding must not
    hand back its bytes.
    """
    try:
        canonical: str | None = os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        canonical = None
    if canonical is not None:
        reason = canonical_path_secret_reason(canonical)
        if reason is not None:
            return reason
    try:
        st = os.fstat(fd)
    except OSError as exc:
        return (
            "a file whose identity this install could not establish "
            f"({exc}), so its own secret files cannot be ruled out"
        )
    try:
        identities = secret_file_identities()
    except SecretIdentityUnreadable as exc:
        # Asked before its base class, which would otherwise answer for it.
        return (
            "a file this install cannot compare with its own secret files, "
            f"because the identity of one of them could not be read ({exc})"
        )
    except InstallOwnerUnresolved as exc:
        return (
            "a file this install cannot compare with its own secret files, "
            f"because the account that owns them could not be established "
            f"({exc})"
        )
    reason = identities.get((st.st_dev, st.st_ino))
    if reason is not None:
        return reason
    if canonical is None:
        return (
            "a file whose canonical path this install could not read, so its "
            "own secret files cannot be ruled out"
        )
    return None


def opened_refusing_install_secrets(path: object, flags: int,
                                    mode: int = 0o666, *,
                                    made: MadeDirectories | None = None) -> int:
    """Open `path`, then refuse if what opened is one of the install's secrets.

    The refusal lands after the open and before any read or write. That order is
    the mechanism: the open is what resolves every spelling of a name, and the
    descriptor it returns is the only thing that can be asked what it actually
    is.

    O_TRUNC is refused outright. Truncation happens inside the open, so a flag
    set that carried it would empty the credential before any check could refuse
    it; a caller that needs an empty file truncates the descriptor this returns.

    O_CREAT never reaches an open that could create the file being asked about
    before it is asked. An existing object is opened without it. Only when
    nothing is there is a file created: the canonical tail of the resolved parent
    plus the new name is asked first, the create carries O_EXCL|O_NOFOLLOW so it
    cannot land on an object that appeared in between or follow a link, and a
    file the check on its new descriptor still refuses is removed again before
    the refusal is raised, by a cleanup that removes that file and nothing else
    (_remove_own_creation). A refused write therefore leaves nothing at the path
    of a credential that did not exist, or reports that it did.

    `made` is what made_parent_directories returned for this path, when the
    caller had the directories made; this call takes it over and closes it.
    When it holds directories, the file is created in the innermost of them
    through that directory's descriptor, and only then are the directories
    handed to the requesting owner (MadeDirectories).

    Under the privileged runner the open runs as root on a path that was
    resolved and checked earlier, by name, and a name in a directory the
    requesting account can write can be replaced in between. There the path is
    opened as it was checked or not at all (_opened_as_checked): one component
    at a time by descriptor, never through a link, a regular file only, and the
    kernel's own path for what opened must be the path that was checked.

    Returns the open descriptor, which the caller owns and must close. Raises
    InstallSecretRefused if the object is one of the install's secret files,
    CreatedFileNotRemoved (an OSError) in its place when a file created for a
    refused write could not be removed, PathNotOpenedAsChecked (an OSError)
    under the runner, and OSError from the open itself.
    """
    if made is not None:
        if made.innermost is not None:
            return _created_in_made_directories(path, flags, mode, made)
        made.close()
    _refuse_truncation(flags)
    if _under_the_privileged_runner():
        return _opened_as_checked(path, flags, mode)
    if not flags & os.O_CREAT:
        return _refusing_install_secrets(os.open(path, flags, mode), path)
    try:
        return _opened_or_created(path, flags, mode)
    except FileExistsError:
        if flags & os.O_EXCL:
            raise
        # Something appeared at the name between the two opens. Once more from
        # the top, so an object that now exists is opened and checked like any
        # other; a second race is reported, not looped on.
        return _opened_or_created(path, flags, mode)


def _refuse_truncation(flags: int) -> None:
    if flags & os.O_TRUNC:
        raise ValueError(
            "opened_refusing_install_secrets refuses O_TRUNC: the open would "
            "empty the file before the refusal could run. Truncate the returned "
            "descriptor instead."
        )


def _refusing_install_secrets(fd: int, path: object) -> int:
    """Return `fd`, or close it and refuse if it is one of the install's secrets."""
    try:
        reason = open_descriptor_secret_reason(fd)
    except BaseException:
        os.close(fd)
        raise
    if reason is not None:
        os.close(fd)
        logger.warning("Refused a tool's open of %s: %s", path, reason)
        raise InstallSecretRefused(reason)
    return fd


def _opened_or_created(path: object, flags: int, mode: int) -> int:
    """Open an existing object without O_CREAT; create only if none is there."""
    if not flags & os.O_EXCL:
        try:
            fd = os.open(path, flags & ~os.O_CREAT, mode)
        except FileNotFoundError:
            pass
        else:
            return _refusing_install_secrets(fd, path)
    return _created_refusing_install_secrets(path, flags, mode)


class PathNotOpenedAsChecked(OSError):
    """Under the privileged runner, what a path names now is not what was checked.

    The path was resolved and checked by name, and a name in a directory the
    requesting account can write can be replaced before the open: by a link, by
    a FIFO, or by anything that is not the regular file the checks were asked
    about. Nothing is read or written then. An OSError, so a consumer reports it
    as a failed read or write.
    """


def _checked_text(path: object) -> str:
    """The path as the checks were asked of it: absolute and already resolved."""
    text = os.fspath(path)
    if not os.path.isabs(text) or os.path.normpath(text) != text:
        raise PathNotOpenedAsChecked(
            f"Under the privileged runner a path is opened only as it was "
            f"resolved for its checks; {text!r} is not an absolute, resolved "
            f"path, so nothing was opened.")
    return text


def _walked_to(directory: str) -> int:
    """Open `directory`, a resolved absolute path, one component at a time from
    /, by descriptor, never through a link (held_open.held_directory). Returns
    an O_PATH descriptor."""
    try:
        return held_directory(directory)
    except HeldOpenRefused as exc:
        raise _not_a_directory_now(exc.filename or directory,
                                   exc.__cause__ or exc) from exc


def _not_a_directory_now(here: str, exc: OSError) -> PathNotOpenedAsChecked:
    return PathNotOpenedAsChecked(
        f"Under the privileged runner a path is opened only as it was resolved "
        f"for its checks, never through a link; {here} is not a directory now "
        f"({exc.strerror}), so nothing was opened.")


def _assert_opened_as_checked(fd: int, text: str) -> None:
    """Raise PathNotOpenedAsChecked unless `fd` is a regular file whose kernel
    path is `text`, the path the checks were asked of."""
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise PathNotOpenedAsChecked(
            f"{text} is not a regular file; under the privileged runner only a "
            f"regular file is opened to be read or written, so nothing was.")
    try:
        canonical = os.readlink(f"/proc/self/fd/{fd}")
    except OSError as exc:
        raise PathNotOpenedAsChecked(
            f"What was opened at {text} could not be read back ({exc}), so it "
            f"cannot be shown to be the path that was checked; nothing was read "
            f"or written.") from exc
    if canonical != text:
        raise PathNotOpenedAsChecked(
            f"{text} opened as {canonical}, which is not the path that was "
            f"checked; nothing was read or written.")


def _opened_existing(dir_fd: int, name: str, text: str, flags: int,
                     mode: int) -> int:
    """Open an existing `name` in `dir_fd` as the regular file the hold
    identified (held_open.open_held_at): never through a link, and never by
    running what a FIFO or a device does when it is opened, so a refusal here
    is made before anything was opened. Then check its kernel path is `text`
    and that it is no secret."""
    try:
        fd, _held = open_held_at(dir_fd, name, flags, text)
    except HeldOpenRefused as exc:
        if exc.refusal == "link":
            raise PathNotOpenedAsChecked(
                f"{text} is a link now, and under the privileged runner a path "
                f"is never opened through a link; nothing was read or "
                f"written.") from exc
        if exc.refusal == "kind":
            raise PathNotOpenedAsChecked(
                f"{text} is not a regular file (a FIFO, a socket or a device); "
                f"under the privileged runner only a regular file is opened to "
                f"be read or written, so nothing was.") from exc
        raise PathNotOpenedAsChecked(str(exc)) from exc
    try:
        _assert_opened_as_checked(fd, text)
    except BaseException:
        os.close(fd)
        raise
    return _refusing_install_secrets(fd, text)


def _opened_as_checked(path: object, flags: int, mode: int) -> int:
    """Under the privileged runner: open the resolved path as it was checked.

    The directory is walked from / by descriptor without following a link; an
    existing file is opened without following a link and without waiting on a
    FIFO, and must be a regular file whose kernel path is the path that was
    checked; a new file is created in the walked directory as everywhere else
    (_created_refusing_install_secrets), and the same is asked of it. Anything
    else raises PathNotOpenedAsChecked and nothing is read or written.
    """
    text = _checked_text(path)
    parent, name = os.path.split(text)
    if not name:
        raise PathNotOpenedAsChecked(
            f"{text!r} names no file, so nothing was opened.")
    dir_fd = _walked_to(parent)
    try:
        if not flags & os.O_CREAT:
            return _opened_existing(dir_fd, name, text, flags, mode)
        try:
            return _opened_or_created_in(dir_fd, name, text, flags, mode)
        except FileExistsError:
            if flags & os.O_EXCL:
                raise
            # Something appeared at the name between the two opens. Once more,
            # as everywhere else; a second race is reported, not looped on.
            return _opened_or_created_in(dir_fd, name, text, flags, mode)
    finally:
        os.close(dir_fd)


def _opened_or_created_in(dir_fd: int, name: str, text: str, flags: int,
                          mode: int) -> int:
    """_opened_or_created, in a walked directory (see _opened_as_checked)."""
    if not flags & os.O_EXCL:
        try:
            return _opened_existing(dir_fd, name, text, flags, mode)
        except FileNotFoundError:
            pass
    return _created_refusing_install_secrets(text, flags, mode, parent_fd=dir_fd)


def _created_refusing_install_secrets(path: object, flags: int, mode: int,
                                      parent_fd: int | None = None) -> int:
    """Create a new file at `path`, asking first and checking again after.

    The parent is opened as a directory, and the kernel's own path for it plus
    the new name is what the canonical-tail question is asked of, so a
    parent-directory spelling or a linked parent is answered as what it
    resolves to. The file is then created relative to that directory
    descriptor, exclusively and without following a link at the name. When
    `parent_fd` is given it is that directory, already open (a directory this
    write made, MadeDirectories), and the parent is not looked up by name.
    """
    text = os.fspath(path)
    parent, name = os.path.split(text)
    if name in ("", ".", ".."):
        raise FileNotFoundError(
            f"nothing can be created at {text!r}: it does not end in a file name")
    if parent_fd is None:
        dir_fd = os.open(parent or ".", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    else:
        dir_fd = os.dup(parent_fd)
    try:
        try:
            canonical_parent: str | None = os.readlink(f"/proc/self/fd/{dir_fd}")
        except OSError:
            canonical_parent = None
        if canonical_parent is None:
            reason: str | None = (
                "a file whose directory's canonical path this install could not "
                "read, so its own secret files cannot be ruled out"
            )
        else:
            reason = canonical_path_secret_reason(
                os.path.join(canonical_parent, name))
        if reason is not None:
            logger.warning("Refused a tool's creation of %s: %s", path, reason)
            raise InstallSecretRefused(reason)
        fd = os.open(name, flags | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     mode, dir_fd=dir_fd)
        try:
            reason = open_descriptor_secret_reason(fd)
            if reason is not None:
                logger.warning("Refused a tool's creation of %s: %s", path, reason)
                raise InstallSecretRefused(reason)
            if _under_the_privileged_runner():
                _assert_opened_as_checked(fd, text)
            _hand_to_the_requesting_owner(fd, canonical_parent, path)
        except BaseException:
            # The refusal stands only once the file it created is gone again,
            # and so does a file that could not be handed to its owner or that
            # is not at the path that was checked. Otherwise
            # _remove_own_creation raises CreatedFileNotRemoved, and that is
            # what the caller receives.
            try:
                _remove_own_creation(fd, name, dir_fd, path)
            finally:
                os.close(fd)
            raise
        return fd
    finally:
        os.close(dir_fd)


class CreatedFileNotRemoved(OSError):
    """A file created for a refused write could not be removed again.

    Raised in place of InstallSecretRefused: the refusal held, but the cleanup
    after it did not, and reporting a clean refusal would claim a state the disk
    does not have. An OSError, so a consumer reports it as a failed operation.
    The message says where the created file, or whatever was moved aside to be
    examined, was left.
    """


class CreatedFileNotHandedToOwner(OSError):
    """A file created for the person who asked could not be made theirs.

    Raised once the file has been removed again (when the removal itself fails,
    CreatedFileNotRemoved is raised in its place), so nobody finds a file of
    root's in their own home where they asked for one of their own. An OSError,
    so a consumer reports it as a failed write.
    """


#: The mode of a file created for the requesting owner in their own home: theirs
#: to read and write, and nobody else's.
_OWNER_FILE_MODE = 0o600

#: The mode of a directory made for the requesting owner in their own home:
#: theirs to enter, list and change, and nobody else's.
_OWNER_DIRECTORY_MODE = 0o700


def _under_the_privileged_runner() -> bool:
    """Whether this process is the privileged runner acting for an account.

    PKEXEC_UID is believed only in a process that runs as root, where the runner
    is what set it, the rule _owning_home states.
    """
    return os.getuid() == 0 and "PKEXEC_UID" in os.environ


def _requesting_owner() -> tuple[int, int, str]:
    """The account the privileged runner acts for: its uid, its primary group,
    and its home as that path resolves. Raises KeyError or ValueError when the
    account cannot be established."""
    uid = int(os.environ["PKEXEC_UID"])
    entry = pwd.getpwuid(uid)
    return uid, entry.pw_gid, os.path.realpath(entry.pw_dir)


def _at_or_below(canonical: str, home: str) -> bool:
    """Whether a resolved path is the resolved home or below it, compared by
    whole components. Nothing is below a home of /."""
    if home == "/":
        return False
    return canonical == home or canonical.startswith(home + os.sep)


def _hand_to_the_requesting_owner(fd: int, canonical_parent: str | None,
                                  path: object) -> None:
    """Make a file this process just created below the requester's home theirs.

    Under the privileged runner the process is root, so what it creates is
    root's: root:root with the mode the umask leaves. In the home of the person
    who asked, that is a file they cannot change or remove and every local
    account can read. A new file whose directory resolves to that home, or below
    it, is therefore handed to the requesting account's uid and primary group
    with mode 0600, through the descriptor, so no name is looked up again. The
    directory is not touched. A file that already existed never reaches here,
    and a file anywhere else keeps the behaviour it had.

    PKEXEC_UID is believed only in a process that runs as root, the rule
    _owning_home states. The home is compared as the path it resolves to, by
    whole components. Any failure raises CreatedFileNotHandedToOwner, and the
    caller removes the file rather than leave it as root's.
    """
    if not _under_the_privileged_runner():
        return
    try:
        uid, gid, home = _requesting_owner()
        if canonical_parent is None or not _at_or_below(canonical_parent, home):
            return
        os.fchown(fd, uid, gid)
        os.fchmod(fd, _OWNER_FILE_MODE)
    except Exception as exc:  # noqa: BLE001 — every failure is this failure
        # Whatever the hand-over raised, the file is not the owner's, and the
        # write must say so rather than escape as an error of another kind.
        logger.error("The file created at %s could not be handed to the account "
                     "that asked for it: %s", path, exc)
        raise CreatedFileNotHandedToOwner(
            f"The file created at {path} could not be handed to the account that "
            f"asked for it ({exc}); it was removed again and nothing was "
            f"written.") from exc
    logger.info("Handed the new file %s to uid %d with mode 0600", path, uid)


#: The start of the private name a refused creation is moved to before it is
#: examined and removed; the rest is random (see _remove_own_creation).
_ASIDE_PREFIX = ".intergen-refused-"


def _not_removed(message: str) -> CreatedFileNotRemoved:
    logger.error("%s", message)
    return CreatedFileNotRemoved(message)


def _remove_own_creation(fd: int, name: str, dir_fd: int, path: object) -> None:
    """Remove the file `fd` created at `name`, and nothing else.

    No step decides on the name and then acts on the name again, because the
    name can be pointed at another object in between. Whatever is at `name` is
    first MOVED, by one rename inside the directory descriptor, to a private name
    of random characters, and only then compared with `fd` by identity:

      * the file `fd` created is unlinked at the private name;
      * anything else is a replacement made after the creation. It is linked
        back at `name`, a link that fails rather than replace whatever is at
        `name` by then, and otherwise stays at the private name.

    The unlink after the comparison names the private name, so it meets an
    object other than the one compared only if an account that can write this
    directory, and so could remove that object itself, put it there.

    Anything short of removing the created file raises CreatedFileNotRemoved,
    saying where things were left.
    """
    try:
        held = os.fstat(fd)
    except OSError as exc:
        raise _not_removed(
            f"The file created at {path} for a refused write is left there: its "
            f"identity could not be read ({exc}).") from exc
    aside = _ASIDE_PREFIX + secrets.token_hex(16)
    aside_path = os.path.join(os.path.dirname(os.fspath(path)), aside)
    try:
        os.rename(name, aside, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except FileNotFoundError as exc:
        raise _not_removed(
            f"Nothing is at {path} any more: the file created there for a "
            f"refused write was moved elsewhere and was not removed.") from exc
    except OSError as exc:
        raise _not_removed(
            f"The file created at {path} for a refused write is left there: it "
            f"could not be moved aside to be removed ({exc}).") from exc
    try:
        moved = os.stat(aside, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        raise _not_removed(
            f"What was at {path} was moved aside to {aside_path} and could not be "
            f"examined there ({exc}); it is left there.") from exc
    if (moved.st_dev, moved.st_ino) == (held.st_dev, held.st_ino):
        try:
            os.unlink(aside, dir_fd=dir_fd)
        except OSError as exc:
            raise _not_removed(
                f"The file created at {path} for a refused write was moved aside "
                f"to {aside_path} and could not be removed there ({exc}).") from exc
        # Removing a name removes the file only when it was the last one. The
        # held descriptor says how many the file still has; anything but none
        # means the file is still there under another name.
        try:
            remaining = os.fstat(fd).st_nlink
        except OSError as exc:
            raise _not_removed(
                f"The file created at {path} for a refused write had its name "
                f"removed, and whether it has another could not be read ({exc}).") from exc
        if remaining:
            raise _not_removed(
                f"The file created at {path} for a refused write still has "
                f"{remaining} other name{'s' if remaining != 1 else ''} after its "
                f"own was removed, so it was not removed.")
        return
    try:
        os.link(aside, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
                follow_symlinks=False)
    except OSError as exc:
        raise _not_removed(
            f"{path} was replaced after a file was created there for a refused "
            f"write. The replacement could not be put back ({exc}) and is at "
            f"{aside_path}; the created file was moved elsewhere and was not "
            f"removed.") from exc
    try:
        os.unlink(aside, dir_fd=dir_fd)
    except OSError as exc:
        raise _not_removed(
            f"{path} was replaced after a file was created there for a refused "
            f"write. The replacement is back at {path} and still also at "
            f"{aside_path} ({exc}); the created file was moved elsewhere and was "
            f"not removed.") from exc
    raise _not_removed(
        f"{path} was replaced after a file was created there for a refused write. "
        f"The replacement is back at {path}; the created file was moved elsewhere "
        f"and was not removed.")


class CreatedDirectoryNotHandedToOwner(OSError):
    """A directory made for the person who asked could not be made theirs.

    Raised once what the write created, the file and the empty directories it
    made, has been removed again (when a removal itself fails,
    CreatedFileNotRemoved or CreatedDirectoryNotRemoved is raised in its place),
    so nobody finds a directory of root's in their own home where they asked for
    a file of their own. An OSError, so a consumer reports it as a failed write.
    """


class CreatedDirectoryNotRemoved(OSError):
    """A directory made for a write that then failed could not be removed again.

    Raised in place of the failure that called for the removal: reporting that
    the write took everything it made with it would claim a state the disk does
    not have. The message says which directories were left, and where.
    """


class _NotHandedOver(Exception):
    """The first directory of a MadeDirectories that could not be handed over."""

    def __init__(self, path: str, error: BaseException) -> None:
        super().__init__(path, error)
        self.path = path
        self.error = error


class MadeDirectories:
    """The directories one privileged write made on the way to its file, held open.

    made_parent_directories returns one and opened_refusing_install_secrets takes
    it over. Each directory was made by this process relative to the descriptor
    of the one above it and is held by its own descriptor, so nothing after its
    making looks it up by name. One at or below the requesting owner's home is
    made with mode 0700 and stays root's while the file is created in the
    innermost, so nothing but root can put anything in it before the file is
    there; then each such directory is handed to the owner, the outermost first
    and the innermost last, or, when a hand-over fails, the file and every
    directory made is removed again. One anywhere else is made as before, with
    the mode the umask leaves, and stays root's.
    """

    def __init__(self) -> None:
        # (descriptor of the directory above, name, own descriptor, path, whether
        # it is the owner's), the outermost first; every descriptor here is
        # closed by close().
        self._made: list[tuple[int, str, int, str, bool]] = []
        self._descriptors: list[int] = []
        self._owner: tuple[int, int] | None = None

    @property
    def innermost(self) -> int | None:
        """The descriptor of the directory the file goes in, or None when this
        write made no directory."""
        return self._made[-1][2] if self._made else None

    def close(self) -> None:
        while self._descriptors:
            os.close(self._descriptors.pop())

    def _hand_over(self) -> None:
        """Hand each directory to the requesting owner, the innermost last.
        Raises _NotHandedOver for the first that cannot be."""
        if self._owner is None:
            raise _NotHandedOver(self._made[0][3], RuntimeError(
                "no requesting owner was established for the directories made"))
        uid, gid = self._owner
        for _above, _name, fd, path, for_owner in self._made:
            if not for_owner:
                continue
            try:
                os.fchown(fd, uid, gid)
                os.fchmod(fd, _OWNER_DIRECTORY_MODE)
            except Exception as exc:  # noqa: BLE001 — every failure is this failure
                raise _NotHandedOver(path, exc) from exc
            logger.info("Handed the new directory %s to uid %d with mode 0700",
                        path, uid)

    def _remove(self) -> None:
        """Remove each directory, the innermost first, only while its name still
        holds the directory made (compared by identity) and it is empty. Raises
        CreatedDirectoryNotRemoved for the first that cannot be removed."""
        for index in range(len(self._made) - 1, -1, -1):
            above, name, fd, path, _for_owner = self._made[index]
            left = [made[3] for made in self._made[:index + 1]]
            try:
                held = os.fstat(fd)
                found = os.stat(name, dir_fd=above, follow_symlinks=False)
            except OSError as exc:
                raise _directory_not_removed(path, left, exc) from exc
            if (found.st_dev, found.st_ino) != (held.st_dev, held.st_ino):
                raise _directory_not_removed(
                    path, left, "something else is at its name now")
            try:
                os.rmdir(name, dir_fd=above)
            except OSError as exc:
                raise _directory_not_removed(path, left, exc) from exc

    def _settle(self) -> None:
        """After a write that left no file of its own: the directories stay, and
        are the owner's. A hand-over that fails removes them and raises
        CreatedDirectoryNotHandedToOwner."""
        try:
            self._hand_over()
        except _NotHandedOver as failure:
            self._remove()
            raise _directory_not_handed(failure) from failure.error


def _directory_not_handed(failure: _NotHandedOver) -> CreatedDirectoryNotHandedToOwner:
    logger.error("The directory created at %s could not be handed to the account "
                 "that asked for it: %s", failure.path, failure.error)
    return CreatedDirectoryNotHandedToOwner(
        f"The directory created at {failure.path} could not be handed to the "
        f"account that asked for it ({failure.error}); what this write created "
        f"was removed again and nothing was written.")


def _directory_not_removed(path: str, left: list[str],
                           why: object) -> CreatedDirectoryNotRemoved:
    message = (
        f"The directory created at {path} for a write that failed was not "
        f"removed ({why}); left in place: {', '.join(left)}.")
    logger.error("%s", message)
    return CreatedDirectoryNotRemoved(message)


def made_parent_directories(path: object) -> MadeDirectories:
    """Make the directories missing above `path`, as write_file's create_dirs asks.

    Under the privileged runner the path, already resolved by the caller, is
    walked one component at a time by descriptor without following a link, and
    each missing directory is made relative to the one above it, opened without
    following a link, and checked to be the empty directory this process just
    made at that path. One at or below the requesting owner's home is made with
    mode 0700 and later handed to the owner; one anywhere else is made with the
    mode the umask leaves and stays root's, as before. What this returns holds
    them for opened_refusing_install_secrets, which creates the file in the
    innermost and then settles them (MadeDirectories). A directory that already
    exists is passed through, untouched. Outside the runner the directories are
    made as before, by Path.mkdir(parents=True, exist_ok=True), and nothing is
    held.

    Raises OSError when a directory cannot be made, PathNotOpenedAsChecked among
    them when a name on the way is a link now; any directory this call made by
    then is handed to the owner first, or removed when that fails too.
    """
    if not _under_the_privileged_runner():
        Path(os.path.dirname(os.fspath(path))).mkdir(parents=True, exist_ok=True)
        return MadeDirectories()
    parent = os.path.dirname(_checked_text(path))
    try:
        uid, gid, home = _requesting_owner()
    except (KeyError, ValueError) as exc:
        raise OSError(
            f"the account that asked for this write could not be established "
            f"({exc}), so no directory was made for it") from exc
    made = MadeDirectories()
    made._owner = (uid, gid)
    try:
        _walk_making(made, parent, home)
    except BaseException:
        try:
            if made.innermost is not None:
                made._settle()
        finally:
            made.close()
        raise
    if made.innermost is None:
        made.close()
    return made


def _walk_making(made: MadeDirectories, parent: str, home: str) -> None:
    """Walk `parent` from / by descriptor, making what is missing (see above)."""
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    made._descriptors.append(fd)
    walked = "/"
    parts = [part for part in parent.split(os.sep) if part]
    missing = len(parts)
    for index, part in enumerate(parts):
        try:
            below = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            missing = index
            break
        except OSError as exc:
            if exc.errno in (errno.ENOTDIR, errno.ELOOP):
                raise _not_a_directory_now(os.path.join(walked, part), exc) from exc
            raise
        # Only the directory above the first one made is needed later; every
        # descriptor before it is closed as the walk passes it.
        os.close(made._descriptors.pop())
        made._descriptors.append(below)
        fd, walked = below, os.path.join(walked, part)
    for part in parts[missing:]:
        here = os.path.join(walked, part)
        for_owner = _at_or_below(here, home)
        os.mkdir(part, _OWNER_DIRECTORY_MODE if for_owner else 0o777, dir_fd=fd)
        try:
            below = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=fd)
        except OSError as exc:
            raise OSError(
                f"the directory made at {here} could not be opened again ({exc}); "
                f"its name was moved or replaced, and the directory made is left "
                f"wherever it was moved") from exc
        try:
            st = os.fstat(below)
            canonical = os.readlink(f"/proc/self/fd/{below}")
            ours = (st.st_uid == os.geteuid() and not os.listdir(below)
                    and canonical == here)
        except BaseException:
            os.close(below)
            raise
        if not ours:
            os.close(below)
            raise OSError(
                f"the directory made at {here} is not what is there now (found "
                f"at {canonical}, owner {st.st_uid}); its name was moved or "
                f"replaced, the directory made is left wherever it was moved, and "
                f"nothing was written")
        made._descriptors.append(below)
        made._made.append((fd, part, below, here, for_owner))
        fd, walked = below, here


def _created_in_made_directories(path: object, flags: int, mode: int,
                                 made: MadeDirectories) -> int:
    """Create the file in the innermost directory `made` holds, then settle the
    directories: handed to the owner, or removed with the file (MadeDirectories)."""
    try:
        innermost = made.innermost
        try:
            _refuse_truncation(flags)
            fd = _created_refusing_install_secrets(path, flags, mode,
                                                   parent_fd=innermost)
        except CreatedFileNotHandedToOwner:
            # The file is gone again already; the directories made for it follow.
            made._remove()
            raise
        except BaseException:
            made._settle()
            raise
        try:
            made._hand_over()
        except _NotHandedOver as failure:
            try:
                _remove_own_creation(fd, os.path.basename(os.fspath(path)),
                                     innermost, path)
            finally:
                os.close(fd)
            made._remove()
            raise _directory_not_handed(failure) from failure.error
        return fd
    finally:
        made.close()


def read_text_refusing_install_secrets(path: object, *,
                                       errors: str = "replace") -> str:
    """Read `path` as text, unless what opens is one of the install's secrets."""
    fd = opened_refusing_install_secrets(path, os.O_RDONLY | os.O_CLOEXEC)
    with os.fdopen(fd, "r", errors=errors) as handle:
        return handle.read()


def dispatch_key_path_for_user(username: str) -> Path:
    """Path to a specific user's signing key (root verify side).

    The pkexec runner exports PKEXEC_USER; root resolves the caller's home via
    the password database and reads the key there. This MUST be used root-side
    instead of dispatch_key_path()/Path.home(), because the runner sets
    HOME=/root — Path.home() root-side would wrongly point at /root/.config.
    """
    try:
        home = Path(pwd.getpwnam(username).pw_dir)
    except KeyError as exc:
        raise KeyError_(f"cannot resolve home for user {username!r}: {exc}") from exc
    return home.joinpath(*KEY_RELATIVE_PATH)


def ensure_dispatch_key(path: Path | None = None) -> str:
    """Return the signing key, generating it (0o600) on first run if absent.

    Gen-on-first-run, do-NOT-clobber: a signing key must be stable across daemon
    restarts (rotating it silently would invalidate any in-flight token). This is
    the setup entry point — call it from run_setup alongside the web-token.
    Returns the hex key string.
    """
    if path is None:
        path = dispatch_key_path()
    if path.exists():
        return load_dispatch_key(path)
    return generate_dispatch_key(path)


def generate_dispatch_key(path: Path | None = None) -> str:
    """(Re)generate the signing key at path with 0o600 perms, return the hex key.

    Mirrors setup.py:_generate_auth_token. Prefer ensure_dispatch_key() for
    first-run setup; call this directly only for an explicit, intentional key
    rotation (which invalidates outstanding tokens by design).
    """
    if path is None:
        path = dispatch_key_path()
    key = secrets.token_hex(KEY_BYTES)
    private_dir(path.parent)
    # Create with restrictive perms from the outset (avoid a brief world-readable
    # window between write and chmod): open via os.open with mode, then write.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, KEY_MODE)
    try:
        os.write(fd, key.encode("ascii"))
    finally:
        os.close(fd)
    os.chmod(path, KEY_MODE)  # belt-and-suspenders vs a permissive umask race
    logger.info("Dispatch signing key generated at %s", path)
    return key


def load_dispatch_key(path: Path | None = None) -> str:
    """Read + validate the signing key. Fails CLOSED on missing file, bad perms,
    or malformed content (raises KeyError_)."""
    if path is None:
        path = dispatch_key_path()
    try:
        st = path.stat()
    except OSError as exc:
        raise KeyError_(f"dispatch key not found at {path}: {exc}") from exc
    # Reject group/world-accessible keys — a key readable beyond the owner is a
    # tamper/mis-install signal; do not sign or verify with it.
    if st.st_mode & 0o077:
        raise KeyError_(
            f"dispatch key {path} has insecure permissions "
            f"{oct(st.st_mode & 0o777)}; expected {oct(KEY_MODE)}"
        )
    key = path.read_text().strip()
    if len(key) != KEY_BYTES * 2 or not _is_hex(key):
        raise KeyError_(
            f"dispatch key {path} is malformed (expected {KEY_BYTES * 2} hex chars)"
        )
    return key


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


# --- Token payload -----------------------------------------------------------

@dataclass(frozen=True)
class TokenPayload:
    """The verified, decoded contents of a dispatch token. Returned by
    verify_token on success so the root side can read the approval-nonce for its
    consumed-nonce (replay) store."""

    version: int
    tool: str
    args_sha256: str
    nonce: str
    uid: int
    iat: int
    exp: int


# --- Mint (user daemon side) -------------------------------------------------

def mint_token(
    tool: str,
    args: dict,
    uid: int,
    *,
    key: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    """Mint a single-use token binding a human approval to THIS privileged call.

    Call this in the user daemon immediately AFTER the human approves the action
    in the review modal (never on the AI's say-so). The returned token is passed
    to the pkexec runner, which re-exports it for root-side verify_token.

    Args:
        tool: the privileged tool name (must match what root receives on argv).
        args: the exact argument object the human approved (digest-bound).
        uid: the calling user's uid (root checks against PKEXEC_UID).
        key: signing key hex; loaded from the user's key file if None.
        ttl_seconds: token lifetime; DEFAULT_TTL_SECONDS.
        now: epoch seconds override (testing); time.time() if None.
        nonce: approval-nonce override (testing); a fresh 128-bit nonce if None.

    Returns:
        The token string `<b64url(payload_json)>.<hmac_hex>`.
    """
    if key is None:
        key = load_dispatch_key()
    if now is None:
        now = int(time.time())
    if nonce is None:
        nonce = secrets.token_hex(16)
    payload = {
        "v": TOKEN_VERSION,
        "tool": tool,
        "args_sha256": args_digest(args),
        "nonce": nonce,
        "uid": int(uid),
        "iat": int(now),
        "exp": int(now) + int(ttl_seconds),
    }
    body = _b64url_encode(_canonical_json(payload).encode("utf-8"))
    mac = _sign(key, body)
    return f"{body}.{mac}"


# --- Verify (root side primitive; wired into privileged_dispatch) --

def verify_token(
    token: str,
    tool: str,
    args: dict,
    uid: int,
    *,
    key: str | None = None,
    username: str | None = None,
    now: int | None = None,
    leeway_seconds: int = 0,
) -> TokenPayload:
    """Verify a dispatch token against THIS (tool, args, uid). Fails CLOSED.

    Performs: parse -> HMAC signature check (constant-time) -> version check ->
    binding check (tool + args digest + uid) -> freshness check (iat/exp). On
    success returns the decoded TokenPayload so the caller can enforce
    SINGLE-USE via its own consumed-nonce store (this primitive is stateless;
    replay protection lives root-side where the persistent nonce store is).

    Args:
        token: the token string, read root-side out of the request file.
        tool / args / uid: what root actually received — the token must bind to
            exactly these.
        key: signing key hex. If None, resolve via `username` (root side) using
            dispatch_key_path_for_user; falls back to the current user's key when
            neither is given (user-context self-test).
        username: the calling user (PKEXEC_USER) for root-side key resolution.
        now: epoch override (testing).
        leeway_seconds: optional clock-skew tolerance on iat/exp (default 0;
            single machine, one clock — no skew expected).

    Raises:
        MalformedToken, BadSignature, BindingMismatch, TokenExpired, KeyError_.
    """
    if key is None:
        if username is not None:
            key = load_dispatch_key(dispatch_key_path_for_user(username))
        else:
            key = load_dispatch_key()

    # 1. Structural parse.
    try:
        body, mac = token.rsplit(".", 1)
    except (ValueError, AttributeError) as exc:
        raise MalformedToken(f"token is not <body>.<mac>: {exc}") from exc
    if not body or not mac:
        raise MalformedToken("token has an empty body or mac segment")

    # 2. Signature FIRST, constant-time — never inspect an unauthenticated
    #    payload's fields. Recompute over the exact transmitted body string so
    #    there is no canonicalization ambiguity on the verify side.
    expected = _sign(key, body)
    if not hmac.compare_digest(mac, expected):
        raise BadSignature("dispatch token HMAC does not match this install's key")

    # 3. Decode the now-authenticated payload.
    try:
        payload_raw = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise MalformedToken(f"authenticated payload is not valid JSON: {exc}") from exc
    if not isinstance(payload_raw, dict):
        raise MalformedToken("authenticated payload is not a JSON object")

    try:
        payload = TokenPayload(
            version=int(payload_raw["v"]),
            tool=str(payload_raw["tool"]),
            args_sha256=str(payload_raw["args_sha256"]),
            nonce=str(payload_raw["nonce"]),
            uid=int(payload_raw["uid"]),
            iat=int(payload_raw["iat"]),
            exp=int(payload_raw["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedToken(f"authenticated payload missing/invalid field: {exc}") from exc

    if payload.version != TOKEN_VERSION:
        raise MalformedToken(
            f"token version {payload.version} != supported {TOKEN_VERSION}"
        )

    # 4. Binding — the signature proves WE minted it; these checks prove it was
    #    minted for THIS call (not replayed against a different tool/args/user).
    if payload.tool != tool:
        raise BindingMismatch(
            f"token tool {payload.tool!r} != dispatched tool {tool!r}"
        )
    if not hmac.compare_digest(payload.args_sha256, args_digest(args)):
        raise BindingMismatch("token args digest does not match dispatched arguments")
    if payload.uid != int(uid):
        raise BindingMismatch(f"token uid {payload.uid} != caller uid {uid}")

    # 5. Freshness.
    if now is None:
        now = int(time.time())
    if now + leeway_seconds < payload.iat:
        raise TokenExpired(f"token not yet valid (iat {payload.iat} > now {now})")
    if now - leeway_seconds > payload.exp:
        raise TokenExpired(f"token expired (exp {payload.exp} < now {now})")

    return payload


def _sign(key: str, body: str) -> str:
    """HMAC-SHA256 of the token body under the install key, hex-encoded."""
    return hmac.new(key.encode("ascii"), body.encode("ascii"), hashlib.sha256).hexdigest()
