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

THAT LAST CLAUSE IS ENFORCED, NOT ASSUMED, AS OF 2026-10-01. A ToolCall can read
any file the daemon's own account can read, and this key is owned by that account,
so "it cannot read the key file" was an intention until something refused the
read: a file-reading tool dispatched at the key returned it in the tool result and
in the audit record, with no review surface consulted. `secret_file_reason` below
names the install's own secret files, and the dispatcher consults it for every
tool call before any tool runs — so the sentence above is now a property of the
code rather than a claim about the attacker.

DIVISION OF LABOR (AI-6 build, branch off master 5ce0dc41):
  * User side (this module + the mint call-site) — key gen-on-first-run, the canonical
    token format, `mint_token`, and the shared `verify_token` crypto primitive.
  * Root side — wire `verify_token` into `privileged_dispatch.main`, add
    the persistent consumed-nonce store, read the key via PKEXEC_USER's home
    (NOT HOME=/root), and the strong run_command classifier (AI-4).
  * Verification — an independent review of both sides.

This module is stdlib-only (hmac, hashlib, secrets, json, base64, time, os, pwd,
pathlib) so it is auditable in isolation and carries no third-party trust.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import pwd
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

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
#: the reason a refusal states. The threat model in this module's header rests on
#: a tool call being unable to read the key; `secret_file_reason` below is what
#: makes that true rather than intended, and the dispatcher consults it for every
#: tool call. The web-token is here for the same reason the header cites it as the
#: precedent: it is the credential the browser surface authenticates with.
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
    """The home directory of the account this process runs as.

    Resolved through the password database rather than HOME, for the reason
    dispatch_key_path above states: a stripped or forged HOME in the daemon's
    environment must not be able to move where the install's own files are
    believed to live. One function, so the mint side and the file boundary below
    cannot come to different answers.
    """
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def secret_file_reason(candidate: object) -> str | None:
    """Why this value names one of the install's own secret files, or None.

    THE THREAT MODEL IN THIS MODULE'S HEADER IS WHAT THIS FUNCTION ENFORCES. That
    header justifies a file key over the keyring on the ground that prompt
    injection "can only make the LLM emit a ToolCall -- it cannot read the key
    file". A tool call CAN read any file the daemon's own account can read, and
    the key is owned by that account, so the sentence was true only as an
    intention until something refused the read. The dispatcher consults this for
    every tool call, before any tool runs.

    Matching is on the path's TAIL, not on a resolved home, for three reasons.
    The daemon may run for any account; a test or a staged install puts the same
    files under a different base; and a path arriving from a model is not
    trustworthy enough to resolve against one expected home and dismiss when it
    does not match. The conservative direction is to refuse a file that is
    shaped like the secret wherever it sits.

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

    for relative, reason in SECRET_RELATIVE_PATHS.items():
        tail = "/".join(relative)
        # (a) the value IS the file, by its own path tail. expanduser covers the
        # tilde form a person or a model writes; no resolve(), which would touch
        # the filesystem for a value that may be hostile and may not exist.
        try:
            parts = Path(text).expanduser().parts
        except (ValueError, RuntimeError):
            parts = ()
        if len(parts) >= len(relative) and tuple(parts[-len(relative):]) == relative:
            return reason
        # (b) the value NAMES the file inside a longer string -- a command line,
        # for instance. The tail is specific enough that an ordinary command
        # cannot contain it by accident, and a command that does contain it is
        # asking for exactly what this refuses.
        if tail in text.replace("\\", "/"):
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


def secret_file_identities() -> dict[tuple[int, int], str]:
    """The (device, inode) identity of each of this install's secret files.

    The leg a name cannot launder. A hard link gives one file a second name whose
    own canonical path says nothing about what it points at, so a comparison on
    that name finds nothing to refuse; the identity of the object is the same
    either way.

    This leg speaks only for the files of THIS install, since it has to resolve
    them to stat them; the tail comparison above is what covers a staged install
    or a test under another base. A file that does not exist contributes nothing:
    an install before its first run has no key to protect yet.
    """
    identities: dict[tuple[int, int], str] = {}
    try:
        home = _owning_home()
    except (KeyError, OSError):
        return identities
    for relative, reason in SECRET_RELATIVE_PATHS.items():
        try:
            st = home.joinpath(*relative).stat()
        except OSError:
            continue
        identities[(st.st_dev, st.st_ino)] = reason
    return identities


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
    reason = secret_file_identities().get((st.st_dev, st.st_ino))
    if reason is not None:
        return reason
    if canonical is None:
        return (
            "a file whose canonical path this install could not read, so its "
            "own secret files cannot be ruled out"
        )
    return None


def opened_refusing_install_secrets(path: object, flags: int,
                                    mode: int = 0o666) -> int:
    """Open `path`, then refuse if what opened is one of the install's secrets.

    The refusal lands after the open and before any read or write. That order is
    the mechanism: the open is what resolves every spelling of a name, and the
    descriptor it returns is the only thing that can be asked what it actually
    is.

    O_TRUNC is refused outright. Truncation happens inside the open, so a flag
    set that carried it would empty the credential before any check could refuse
    it; a caller that needs an empty file truncates the descriptor this returns.

    Returns the open descriptor, which the caller owns and must close. Raises
    InstallSecretRefused if the object is one of the install's secret files, and
    OSError from the open itself.
    """
    if flags & os.O_TRUNC:
        raise ValueError(
            "opened_refusing_install_secrets refuses O_TRUNC: the open would "
            "empty the file before the refusal could run. Truncate the returned "
            "descriptor instead."
        )
    fd = os.open(path, flags, mode)
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
