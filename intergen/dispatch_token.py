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

DIVISION OF LABOR (AI-6 build, branch off master 5ce0dc41):
  * User side (this module + the mint call-site) — key gen-on-first-run, the canonical
    token format, `mint_token`, and the shared `verify_token` crypto primitive.
  * Root side — wire `verify_token` into `privileged_dispatch.main`, add
    the persistent consumed-nonce store, read the key via PKEXEC_USER's home
    (NOT HOME=/root), and the strong run_command classifier (AI-4).
  * WC — independent verification.

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

#: Environment variable the pkexec runner re-exports the token into, read by
#: privileged_dispatch root-side. Kept off the Python dispatcher's argv (which is
#: world-visible via /proc/cmdline for the process lifetime); the runner performs
#: the argv->env handoff so the token lives only in the owner+root-readable env.
TOKEN_ENV_VAR = "INTERGEN_DISPATCH_TOKEN"


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
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home.joinpath(*KEY_RELATIVE_PATH)


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
        token: the token string from the pkexec runner's TOKEN_ENV_VAR.
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
