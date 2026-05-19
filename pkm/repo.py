"""pkm repo layer — Remote repository sync, package fetching, and verification.

Repository Structure:
    https://repo.intergenos.org/
    ├── x86_64/
    │   ├── current/              # Symlink to latest atomic-promoted snapshot
    │   │   ├── InterGenOS.db     # Package index (JSON, gzipped)
    │   │   ├── InterGenOS.db.sig # GPG detached signature of the index
    │   │   ├── firefox-138.0-1.igos.tar.gz
    │   │   ├── gimp-2.10.38-1.igos.tar.gz
    │   │   └── ...
    │   ├── _staging-YYYYMMDDTHHMMSSZ/   # Write-side: per-promote staging dirs
    │   └── _previous/                   # Archive of prior snapshots
    └── sources/                   # Optional source tarballs
        └── ...

Index Format (InterGenOS.db — gzipped JSON):
    {
        "version": 1,
        "generated": "2026-04-09T12:00:00Z",
        "arch": "x86_64",
        "package_count": 487,
        "packages": {
            "firefox": {
                "version": "138.0",
                "release": 1,
                "description": "Mozilla Firefox web browser",
                "tier": "extra",
                "depends": ["gtk3", "nss", "dbus-glib"],
                "makedepends": ["rust", "cbindgen", "nodejs"],
                "license": "MPL-2.0",
                "size": 72548096,
                "installed_size": 215000000,
                "sha256": "a1b2c3d4e5f6...",
                "filename": "firefox-138.0-1.igos.tar.gz",
                "build_date": "2026-04-08T10:00:00Z",
                "source_url": "https://archive.mozilla.org/pub/firefox/releases/138.0/source/firefox-138.0.source.tar.xz",
                "security": true,                   # Q7: present iff this version is a security release
                "cves": ["CVE-2026-12345"]          # Q7: CVE IDs patched by this version (hand-curated v1.0; F-002 v1.1 automation)
            },
            ...
        }
    }

Security Model:
    1. Repository index is GPG-signed with the InterGenOS release key
    2. Each package entry includes SHA256 checksum
    3. On sync: verify GPG signature of index
    4. On install: verify SHA256 of downloaded package against signed index
    5. Chain of trust: GPG key → signed index → SHA256 → package file
"""

import configparser
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# L-020: _parse_index references __version__ for the min_pkm_version
# envelope check. pkm/__init__.py:20 defines __version__ = "0.1.0".
# Importing via the package surface keeps this single-sourced.
from . import __version__

from .database import PackageDB


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_CONFIG_PATH = Path("/etc/pkm/repos.conf")
REPO_CACHE_DIR = Path("/var/cache/pkm")
REPO_DB_CACHE = REPO_CACHE_DIR / "db"
REPO_PKG_CACHE = REPO_CACHE_DIR / "packages"
# Q1 (O-002 + O-007): per-package rollback archive cache. Before each
# upgrade target's remove step, the current archive at REPO_PKG_CACHE
# is copied here so the old version can be restored on install failure.
# Survives REPO_PKG_CACHE GC (separate directory) but lives on the same
# filesystem as REPO_CACHE_DIR so shutil.move on restore stays atomic.
REPO_ROLLBACK_DIR = REPO_CACHE_DIR / "rollback"
GPG_KEYRING = Path("/etc/pkm/trusted.gpg")

DEFAULT_REPOS = {
    "intergenos": {
        "url": "https://repo.intergenos.org/x86_64/current",
        "enabled": True,
        "priority": 100,
    }
}


# L-019: anti-rollback + freshness state.
# After every successful sync, the index's `generated` timestamp is
# persisted under PKM_STATE_DIR/<repo>.json. Subsequent syncs refuse
# any new index whose `generated` is OLDER than the recorded last-seen
# value (replay-attack defense even when signature + sha256 are valid).
# A separate ROLLBACK_MAX_AGE bounds how stale ANY index can be (even
# on first sync with no recorded state); operators get a hard refusal
# rather than silently consuming a 6-month-old snapshot.
PKM_STATE_DIR = Path(os.environ.get("PKM_STATE_DIR", "/var/lib/pkm/state"))
# 7 days: matches the audit row's softer alternative (vs 1h hard limit
# which would aggressively fail-closed for users who can't sync hourly).
ROLLBACK_MAX_AGE_SECONDS = 7 * 24 * 3600


def _parse_iso8601(s):
    """Parse an ISO8601 timestamp string to a timezone-aware datetime.

    Accepts the common Z-suffix shape (e.g. "2026-05-19T04:50:00Z") plus
    the explicit +00:00 form. Returns None on parse failure (caller
    treats absent timestamp as fail-closed).
    """
    if not s:
        return None
    try:
        # datetime.fromisoformat handles "+00:00" but not "Z" in <3.11.
        # Normalize the trailing Z form for cross-version safety.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _load_last_seen_state(repo_name):
    """Return the persisted last-seen `generated` timestamp string for
    a repo, or None if no state file exists yet."""
    state_path = PKM_STATE_DIR / f"{repo_name}.json"
    try:
        data = json.loads(state_path.read_text())
        return data.get("last_seen_generated")
    except (OSError, ValueError):
        return None


def _save_last_seen_state(repo_name, generated):
    """Persist the `generated` timestamp for a repo. Atomic write via
    write-to-temp + rename to prevent torn-write windows."""
    try:
        PKM_STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    state_path = PKM_STATE_DIR / f"{repo_name}.json"
    tmp_path = state_path.with_suffix(".json.tmp")
    payload = json.dumps({
        "last_seen_generated": generated,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        tmp_path.write_text(payload)
        os.replace(str(tmp_path), str(state_path))
        return True
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return False


# L-025: pinned release-key fingerprints (canonical source: pkm/release-keys.json).
# _verify_signature parses gpg --status-fd 1 VALIDSIG output and requires
# the signing fingerprint be in this pinned set, even when gpg's verify
# returns success. Defends against the local-root attack where
# /etc/pkm/trusted.gpg is swapped to attacker's keyring and the
# attacker-signed index validates "successfully" against the attacker's
# key. Pin set lives in code (loaded once at module import), not in a
# mutable on-disk file, so local-root can't pivot the pin set without
# rebuilding pkm itself.
_RELEASE_KEYS_PATH = Path(__file__).parent / "release-keys.json"


def _load_pinned_fingerprints():
    """Return frozenset of pinned release-key fingerprints (40-char hex).

    Reads pkm/release-keys.json at module-import time. Returns empty
    frozenset if the file is unreadable or malformed — in which case
    _verify_signature will reject ALL signatures (fail-closed posture
    matches the L-025 audit row intent).
    """
    try:
        data = json.loads(_RELEASE_KEYS_PATH.read_text())
        return frozenset(
            entry["fingerprint"].upper().replace(" ", "")
            for entry in data.get("keys", {}).values()
            if "fingerprint" in entry
        )
    except (OSError, ValueError, KeyError):
        return frozenset()


PINNED_RELEASE_FINGERPRINTS = _load_pinned_fingerprints()


# L-020: schema-version + signature-format envelope.
# Parser refuses to load indexes outside this envelope per Holy-Grail
# fail-closed posture. Without these gates, an attacker with mirror-write
# access (or compromised signing key during a key-rotation window) can
# publish a `version: 2` index that lies about packages to a client that
# silently misparses, OR a `signature_format: cosign` index that bypasses
# the GPG verification path entirely. Defenders need explicit opt-in
# before schema/signature-format migrations are honored.
SUPPORTED_INDEX_VERSIONS = frozenset({1})
SUPPORTED_SIGNATURE_FORMATS = frozenset({"gpg-detached-armored"})


class IndexFormatError(Exception):
    """Repository index format outside this pkm's supported envelope.

    Raised by RepoManager._parse_index when an index declares a version,
    min_pkm_version, or signature_format the current pkm cannot
    safely handle. Caller treats this as a per-repo sync failure
    (fail-closed) rather than a transparent fallback.
    """


def _semver_ge(actual, required):
    """Return True iff actual >= required under simple semver comparison.

    Both are dotted-numeric strings (e.g. "0.1.0", "1.2.3-beta" — beta
    suffix ignored). Used by L-020 min_pkm_version envelope.
    Falls back to string comparison if parsing fails (conservative).
    """
    def _parts(s):
        head = s.split("-", 1)[0]
        try:
            return tuple(int(x) for x in head.split("."))
        except ValueError:
            return None
    a = _parts(actual)
    r = _parts(required)
    if a is None or r is None:
        return actual >= required
    return a >= r


# ---------------------------------------------------------------------------
# Repository index operations
# ---------------------------------------------------------------------------

class RepoIndex:
    """Represents a parsed repository index."""

    def __init__(self, name, url, data):
        self.name = name
        self.url = url
        self.version = data.get("version", 1)
        self.generated = data.get("generated", "")
        self.arch = data.get("arch", "x86_64")
        self.package_count = data.get("package_count", 0)
        self.packages = data.get("packages", {})

    def search(self, term):
        """Search packages by name or description."""
        term = term.lower()
        results = []
        for name, info in self.packages.items():
            if term in name.lower() or term in info.get("description", "").lower():
                results.append({"name": name, **info, "repo": self.name})
        return sorted(results, key=lambda x: x["name"])

    def get_package(self, name):
        """Get package info by name."""
        if name in self.packages:
            return {"name": name, **self.packages[name], "repo": self.name}
        return None

    def list_by_tier(self, tier):
        """List all packages in a tier."""
        return [
            {"name": name, **info, "repo": self.name}
            for name, info in self.packages.items()
            if info.get("tier") == tier
        ]


# ---------------------------------------------------------------------------
# Repository manager
# ---------------------------------------------------------------------------

class RepoManager:
    """Manages repository sync, caching, and package downloads."""

    def __init__(self):
        self.repos = self._load_repos()
        self.indexes = {}  # name -> RepoIndex

        # Ensure cache dirs exist with restrictive permissions (L-021:
        # mode 0700 on /var/cache/pkm/packages so non-root local users
        # can't write or stage attacker archives between SHA256 verify
        # and tar extract). chmod is required because mkdir(mode=) is
        # masked by umask; explicit chmod sets the bits unconditionally.
        REPO_DB_CACHE.mkdir(parents=True, exist_ok=True)
        REPO_PKG_CACHE.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(REPO_PKG_CACHE), 0o700)
        except OSError:
            # Non-fatal: chmod may fail on filesystems that don't
            # support POSIX modes (e.g. Windows test runs). Production
            # pkm runs as root on a POSIX filesystem so chmod succeeds.
            pass

    def _load_repos(self):
        """Load repository configuration from /etc/pkm/repos.conf.

        Format is INI, one [section] per repository:

            [intergenos-current]
            url = https://repo.intergenos.org/x86_64/current/
            enabled = true
            gpg_verify = true   # optional
            priority = 100      # optional

        Fail-closed: if the file exists but cannot be parsed, raise rather
        than silently fall back to DEFAULT_REPOS. Hidden fallback to a
        vendor-chosen URL when the user's config is broken is a Prime
        Directive violation — the user has to be able to see and trust
        what their machine is doing.

        Missing-file is treated as an initial-state default (fresh install
        before user has touched the config); we use DEFAULT_REPOS without
        warning in that case.
        """
        if not REPO_CONFIG_PATH.exists():
            return dict(DEFAULT_REPOS)

        parser = configparser.ConfigParser()
        try:
            with open(REPO_CONFIG_PATH) as f:
                parser.read_file(f)
        except (configparser.Error, OSError) as e:
            sys.stderr.write(
                f"pkm: error parsing {REPO_CONFIG_PATH}: {e}\n"
                f"pkm: refusing to silently fall back to vendor defaults — "
                f"fix the config or remove it to use the shipped default.\n"
            )
            raise

        repos = {}
        for section in parser.sections():
            try:
                url = parser.get(section, "url")
            except (configparser.NoOptionError, configparser.NoSectionError) as e:
                sys.stderr.write(
                    f"pkm: section [{section}] in {REPO_CONFIG_PATH} "
                    f"missing required 'url' key: {e}\n"
                )
                raise
            cfg = {
                "url": url,
                "enabled": parser.getboolean(section, "enabled", fallback=True),
                "priority": parser.getint(section, "priority", fallback=100),
            }
            if parser.has_option(section, "gpg_verify"):
                cfg["gpg_verify"] = parser.getboolean(section, "gpg_verify")
            # Q6 (O-031): optional `mirrors = url1, url2, ...` failover list.
            # Primary url is tried first; mirrors are walked in declaration
            # order after the primary's retry budget exhausts. Static
            # priority — no active probing per the operator-greenlit spec.
            if parser.has_option(section, "mirrors"):
                raw = parser.get(section, "mirrors")
                mirrors = [m.strip() for m in raw.split(",") if m.strip()]
                cfg["mirrors"] = mirrors
            repos[section] = cfg

        if not repos:
            sys.stderr.write(
                f"pkm: {REPO_CONFIG_PATH} contains no [section] entries; "
                f"using shipped DEFAULT_REPOS.\n"
            )
            return dict(DEFAULT_REPOS)

        return repos

    # ----- Sync -----

    def sync(self, repo_name=None):
        """Sync repository index(es) from remote.

        Returns:
            list of (repo_name, success, message) tuples
        """
        results = []
        targets = {repo_name: self.repos[repo_name]} if repo_name else self.repos

        for name, config in targets.items():
            if not config.get("enabled", True):
                results.append((name, True, "skipped (disabled)"))
                continue

            url = config["url"].rstrip("/")
            db_url = f"{url}/InterGenOS.db"
            sig_url = f"{url}/InterGenOS.db.sig"

            try:
                # Download index
                db_path = REPO_DB_CACHE / f"{name}.db"
                sig_path = REPO_DB_CACHE / f"{name}.db.sig"

                try:
                    self._download(db_url, db_path)
                    self._download(sig_url, sig_path)
                except Exception:
                    # Cleanup orphan .db on partial download (C1.2)
                    db_path.unlink(missing_ok=True)
                    sig_path.unlink(missing_ok=True)
                    raise

                # Verify GPG signature — fail closed if keyring missing (C1.1)
                if not GPG_KEYRING.exists():
                    db_path.unlink(missing_ok=True)
                    sig_path.unlink(missing_ok=True)
                    results.append((name, False,
                        "GPG keyring not found — cannot verify index"))
                    continue
                if not self._verify_signature(db_path, sig_path):
                    results.append((name, False,
                        "GPG signature verification FAILED"))
                    db_path.unlink(missing_ok=True)
                    sig_path.unlink(missing_ok=True)
                    continue

                # Parse index
                index = self._parse_index(name, config["url"], db_path)
                self.indexes[name] = index
                results.append((
                    name, True,
                    f"synced — {index.package_count} packages, "
                    f"generated {index.generated}"
                ))

            except IndexFormatError as e:
                # L-020: schema-envelope rejection. Fail-closed per-repo;
                # leave the cached artifact in place so an operator can
                # inspect what was rejected without re-downloading.
                results.append((name, False, f"index rejected: {e}"))
            except urllib.error.URLError as e:
                results.append((name, False, f"network error: {e.reason}"))
                db_path.unlink(missing_ok=True)
                sig_path.unlink(missing_ok=True)
            except Exception as e:
                results.append((name, False, f"error: {e}"))
                db_path.unlink(missing_ok=True)
                sig_path.unlink(missing_ok=True)

        return results

    # Q6 (O-024): retry + exponential backoff schedule. Three attempts
    # per mirror with 2s / 8s / 30s gaps. Each retry attempts HTTP Range
    # resume from previously-written bytes, so a flaky connection that
    # dropped at 95% does NOT restart from byte 0.
    _DOWNLOAD_RETRY_BACKOFF = (2, 8, 30)
    _DOWNLOAD_TIMEOUT_SECONDS = 30

    def _download(self, url, dest, max_retries=None, backoff_seq=None,
                  partial_path=None):
        """Download a file with retry + exponential backoff + Range resume.

        Args:
            url: source URL.
            dest: final destination path. The completed download is moved
                here atomically once the full body is received.
            max_retries: override attempts (default: 3 per
                _DOWNLOAD_RETRY_BACKOFF).
            backoff_seq: override sleep schedule (default: 2s, 8s, 30s).
            partial_path: optional Path for the resumable partial. Default
                is REPO_CACHE_DIR/partial/<dest.name>.part so concurrent
                mirror failover attempts can share progress.

        Raises:
            urllib.error.URLError / OSError after all retries exhaust.
        """
        max_retries = max_retries or len(self._DOWNLOAD_RETRY_BACKOFF)
        backoff_seq = backoff_seq or self._DOWNLOAD_RETRY_BACKOFF

        dest = Path(dest)
        if partial_path is None:
            partial_dir = REPO_CACHE_DIR / "partial"
            partial_dir.mkdir(parents=True, exist_ok=True)
            partial_path = partial_dir / (dest.name + ".part")
        else:
            partial_path = Path(partial_path)
            partial_path.parent.mkdir(parents=True, exist_ok=True)

        last_exc = None
        for attempt in range(max_retries):
            if attempt > 0:
                sleep_sec = backoff_seq[min(attempt - 1, len(backoff_seq) - 1)]
                time.sleep(sleep_sec)
            try:
                offset = partial_path.stat().st_size if partial_path.exists() else 0
                headers = {"User-Agent": f"pkm/{__version__} (InterGenOS)"}
                if offset > 0:
                    headers["Range"] = f"bytes={offset}-"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(
                    req, timeout=self._DOWNLOAD_TIMEOUT_SECONDS,
                ) as resp:
                    # 206 Partial Content honors our Range request — append.
                    # 200 OK means the server ignored Range — restart from 0
                    # (re-truncate partial). Some mirrors serve identical
                    # content via 200 regardless; safer to start fresh than
                    # concatenate two full bodies.
                    if offset > 0 and resp.status == 206:
                        mode = "ab"
                    else:
                        mode = "wb"
                    with open(partial_path, mode) as f:
                        shutil.copyfileobj(resp, f)
                # Atomic-on-same-filesystem move once the body is complete.
                shutil.move(str(partial_path), str(dest))
                return
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                last_exc = e
                continue

        # Retries exhausted. Leave the partial in place for the NEXT
        # mirror's attempt OR for a future call that may resume — the
        # range request will pick up wherever the bytes stopped.
        raise last_exc if last_exc else urllib.error.URLError(
            f"download failed after {max_retries} retries with no exception"
        )

    def _mirror_urls_for_pkg(self, pkg, filename):
        """Build the ordered URL list for downloading `filename` from `pkg`.

        Returns:
            list[str] — primary URL first, then any mirror URLs declared
            on the repo stanza. Each URL is the fully-qualified archive
            location (base + "/" + filename, normalized for trailing /).
        """
        repo_cfg = self.repos.get(pkg.get("repo"), {})
        urls = []
        # Primary URL: either the package's repo URL or pkg['repo'] when
        # the package metadata itself carries the absolute URL form.
        if "://" in pkg.get("repo", ""):
            base = pkg["repo"]
        else:
            base = repo_cfg.get("url", "")
        if base:
            urls.append(f"{base.rstrip('/')}/{filename}")
        for mirror in repo_cfg.get("mirrors", []):
            urls.append(f"{mirror.rstrip('/')}/{filename}")
        return urls

    def _verify_signature(self, data_path, sig_path):
        """Verify GPG detached signature against PINNED_RELEASE_FINGERPRINTS.

        L-025: even if gpg-verify returns success against the on-disk
        keyring (which a local-root attacker can swap), we require the
        signing fingerprint to be in the pinned release-keys.json set
        baked into pkm at import time. --status-fd 1 produces a parseable
        VALIDSIG line carrying the signing-key fingerprint; we match
        against the frozenset PINNED_RELEASE_FINGERPRINTS.

        Returns True iff gpg --verify succeeds AND signing fingerprint
        is pinned. Fails closed in all other cases (gpg failure, no
        VALIDSIG line, non-pinned fingerprint, empty pin set).
        """
        result = subprocess.run(
            ["gpg", "--no-default-keyring", "--keyring", str(GPG_KEYRING),
             "--status-fd", "1",
             "--verify", str(sig_path), str(data_path)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False

        # Status-fd line shape (per gpg(1) DETAILS), 0-based token indexing:
        #   parts[0]  = "[GNUPG:]"
        #   parts[1]  = "VALIDSIG"
        #   parts[2]  = SIGNING-KEY-FP    (the subkey that produced the sig)
        #   parts[3]  = SIGDATE
        #   parts[4]  = SIGTIME
        #   parts[5]  = EXPIREDATE
        #   parts[6]  = SIG-VERSION
        #   parts[7]  = RESERVED
        #   parts[8]  = PUBKEY-ALGO
        #   parts[9]  = HASH-ALGO
        #   parts[10] = SIG-CLASS         (typically "00" for binary sig)
        #   parts[11] = PRIMARY-KEY-FP    (the master that certifies the subkey)
        for line in result.stdout.splitlines():
            if not line.startswith("[GNUPG:] VALIDSIG "):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            signing_fp = parts[2].upper()
            # Prefer primary-key FP at parts[11] when present (handles
            # subkey-signed artifacts — common with hardware tokens that
            # carry signing subkeys of a master that is the long-term
            # trust anchor). Requires at least 12 tokens so parts[11]
            # exists.
            if len(parts) >= 12:
                primary_fp = parts[11].upper()
                if primary_fp in PINNED_RELEASE_FINGERPRINTS:
                    return True
            if signing_fp in PINNED_RELEASE_FINGERPRINTS:
                return True

        # gpg-verify succeeded but no pinned fingerprint found in the
        # status-fd stream. Local-root keyring-swap class attack defeated.
        return False

    def _parse_index(self, name, url, db_path):
        """Parse a gzipped JSON repository index.

        L-020: enforces schema-version + min_pkm_version +
        signature_format envelope. Raises IndexFormatError on any
        out-of-envelope value; caller treats as fail-closed sync error.

        L-019: enforces anti-rollback (`generated` monotonic vs persisted
        last-seen state) + freshness window (ROLLBACK_MAX_AGE_SECONDS) +
        Valid-Until (signed expiry). Raises IndexFormatError on rollback
        attempt, stale index, or expired Valid-Until.
        """
        with gzip.open(db_path, "rt", encoding="utf-8") as f:
            data = json.load(f)

        # Schema-version: must be in supported set. Pre-fix: any value
        # silently accepted (including future versions that may carry
        # restructured packages dict or new fields the client misparses).
        version = data.get("version")
        if version not in SUPPORTED_INDEX_VERSIONS:
            raise IndexFormatError(
                f"index '{name}' declares version={version!r}; this "
                f"pkm only supports versions "
                f"{sorted(SUPPORTED_INDEX_VERSIONS)}. Refusing to parse "
                f"— upgrade pkm or contact the mirror operator."
            )

        # min_pkm_version: server may require newer pkm. Absence means
        # no minimum required.
        min_required = data.get("min_pkm_version")
        if min_required and not _semver_ge(__version__, min_required):
            raise IndexFormatError(
                f"index '{name}' requires pkm >= {min_required}; this "
                f"pkm is {__version__}. Upgrade pkm before syncing "
                f"this index."
            )

        # signature_format: cryptographic agility envelope. Today's
        # accepted set is just gpg-detached-armored; sigstore / cosign
        # / ed25519 migrations require a pkm version that explicitly
        # opts in by extending SUPPORTED_SIGNATURE_FORMATS.
        sig_format = data.get("signature_format", "gpg-detached-armored")
        if sig_format not in SUPPORTED_SIGNATURE_FORMATS:
            raise IndexFormatError(
                f"index '{name}' declares signature_format="
                f"{sig_format!r}; this pkm only supports "
                f"{sorted(SUPPORTED_SIGNATURE_FORMATS)}. Refusing to "
                f"parse — sigstore / cosign / ed25519 migration "
                f"requires a pkm version that explicitly opts in."
            )

        # L-019 (a): anti-rollback. The `generated` timestamp must be
        # parseable + monotonically non-decreasing across syncs.
        generated_str = data.get("generated")
        generated_dt = _parse_iso8601(generated_str)
        if generated_dt is None:
            raise IndexFormatError(
                f"index '{name}' has missing or malformed `generated` "
                f"timestamp ({generated_str!r}); refusing to parse — "
                f"freshness check requires ISO8601 timestamp."
            )
        now = datetime.now(timezone.utc)
        last_seen = _load_last_seen_state(name)
        last_seen_dt = _parse_iso8601(last_seen)
        if last_seen_dt is not None and generated_dt < last_seen_dt:
            raise IndexFormatError(
                f"index '{name}' generated={generated_str} is OLDER "
                f"than last-seen={last_seen}. Rollback attempt refused; "
                f"check mirror integrity + investigate whether the "
                f"published index was reverted intentionally."
            )

        # L-019 (b): freshness window — index too stale even on first
        # sync. Bounds the worst-case "served a 6-month-old snapshot
        # via cached mirror" scenario.
        age_seconds = (now - generated_dt).total_seconds()
        if age_seconds > ROLLBACK_MAX_AGE_SECONDS:
            raise IndexFormatError(
                f"index '{name}' generated={generated_str} is "
                f"{age_seconds / 86400:.1f} days old (max allowed: "
                f"{ROLLBACK_MAX_AGE_SECONDS // 86400} days). Mirror "
                f"may be abandoned or attacker-controlled. Refusing."
            )

        # L-019 (c): Valid-Until signed expiry. Generators emit this
        # alongside `generated` to bound the window during which an
        # index is considered authoritative (matches apt's Valid-Until
        # convention). Absence means no expiry declared (backward-
        # compatible with pre-L-019 generators).
        valid_until_str = data.get("valid_until")
        if valid_until_str:
            valid_until_dt = _parse_iso8601(valid_until_str)
            if valid_until_dt is None:
                raise IndexFormatError(
                    f"index '{name}' has malformed `valid_until` "
                    f"({valid_until_str!r}); refusing to parse."
                )
            if now > valid_until_dt:
                raise IndexFormatError(
                    f"index '{name}' valid_until={valid_until_str} "
                    f"has expired ({now.isoformat()} > "
                    f"{valid_until_str}). Mirror operator must publish "
                    f"a fresh index; refusing stale snapshot."
                )

        # All envelope + freshness gates passed. Persist last-seen
        # state AFTER all checks succeed so a failed parse doesn't
        # poison subsequent runs.
        _save_last_seen_state(name, generated_str)

        return RepoIndex(name, url, data)

    # ----- Query -----

    def _indexes_by_priority(self):
        """Return self.indexes values sorted by repo priority (descending).

        O-028: dedup logic in search / get_package / list_available iterates
        indexes in priority order so higher-priority repos win on name
        collision. Pre-fix the iteration order was dict-insertion which
        meant a third-party repo loaded before official intergenos could
        shadow it purely by happen-stance of load order. Priority defaults
        to 0 when unset so repos without an explicit priority sort last.
        """
        return sorted(
            self.indexes.values(),
            key=lambda idx: self.repos.get(idx.name, {}).get("priority", 0),
            reverse=True,
        )

    def search(self, term):
        """Search across all synced repos."""
        self._ensure_synced()
        results = []
        for index in self._indexes_by_priority():
            results.extend(index.search(term))
        # Deduplicate — higher priority repo wins (O-028: iteration is
        # priority-ordered so first-seen is highest-priority).
        seen = {}
        for r in results:
            if r["name"] not in seen:
                seen[r["name"]] = r
        return list(seen.values())

    def get_package(self, name):
        """Find a package across all repos. Higher-priority repos win on
        name collision (O-028).
        """
        self._ensure_synced()
        for index in self._indexes_by_priority():
            pkg = index.get_package(name)
            if pkg:
                return pkg
        return None

    def list_available(self, tier=None):
        """List all available packages, optionally filtered by tier.
        Higher-priority repos win on name collision (O-028).
        """
        self._ensure_synced()
        results = []
        seen = set()
        for index in self._indexes_by_priority():
            if tier:
                pkgs = index.list_by_tier(tier)
            else:
                pkgs = [
                    {"name": n, **info, "repo": index.name}
                    for n, info in index.packages.items()
                ]
            for pkg in pkgs:
                if pkg["name"] not in seen:
                    seen.add(pkg["name"])
                    results.append(pkg)
        return sorted(results, key=lambda x: x["name"])

    def _ensure_synced(self):
        """Load cached indexes if not already in memory.
        Re-verifies GPG signature before trusting cached index. (C1.1)
        """
        if self.indexes:
            return

        for name, config in self.repos.items():
            db_path = REPO_DB_CACHE / f"{name}.db"
            sig_path = REPO_DB_CACHE / f"{name}.db.sig"
            if db_path.exists() and sig_path.exists():
                if GPG_KEYRING.exists() and self._verify_signature(db_path, sig_path):
                    try:
                        self.indexes[name] = self._parse_index(
                            name, config["url"], db_path
                        )
                    except Exception:
                        pass
                else:
                    # Cached index can't be verified — delete it
                    db_path.unlink(missing_ok=True)
                    sig_path.unlink(missing_ok=True)

    # ----- Download -----

    def download_package(self, name):
        """Download a package archive, verify checksum.

        Q6 (O-024 + O-031): _download retries 3x with 2s/8s/30s backoff
        per mirror; this method walks the primary URL + repos.conf
        `mirrors = ...` list in declaration order, failing over after
        each mirror's retry budget exhausts. Final all-mirrors-exhausted
        error surfaces the last per-mirror failure reason.

        Returns:
            (success, local_path_or_error_message)
        """
        pkg = self.get_package(name)
        if not pkg:
            return False, f"package '{name}' not found in any repository"

        filename = pkg.get("filename")
        if not filename:
            filename = f"{name}-{pkg['version']}-{pkg.get('release', 1)}.igos.tar.gz"

        urls = self._mirror_urls_for_pkg(pkg, filename)
        if not urls:
            return False, (
                f"no download URL available for {name} (repo "
                f"{pkg.get('repo')!r} has no url + no mirrors)"
            )

        local_path = REPO_PKG_CACHE / filename

        # Use cached if checksum matches
        if local_path.exists():
            if self._verify_checksum(local_path, pkg.get("sha256")):
                return True, str(local_path)
            else:
                local_path.unlink()  # Stale/corrupt cache or missing sha256

        # Q6: walk mirrors in priority order; each mirror gets the full
        # retry-with-backoff budget before failover.
        per_mirror_failures = []
        for url in urls:
            try:
                self._download(url, local_path)
                break
            except Exception as e:
                per_mirror_failures.append(f"{url}: {e}")
                continue
        else:
            # All mirrors exhausted — surface each failure so the user
            # can see which mirrors were tried and why each one failed.
            lines = "\n  ".join(per_mirror_failures)
            return False, (
                f"download failed across {len(urls)} mirror(s):\n  {lines}"
            )

        # Verify checksum
        expected = pkg.get("sha256")
        if not self._verify_checksum(local_path, expected):
            local_path.unlink(missing_ok=True)
            return False, f"SHA256 verification FAILED for {filename}"

        return True, str(local_path)

    def _verify_checksum(self, path, expected):
        """Verify SHA256 checksum of a file.
        Requires exactly 64 lowercase hex characters (SHA256 hex digest).
        Rejects None, empty string, wrong-length, non-hex values. (H1)
        """
        if not isinstance(expected, str) or len(expected) != 64:
            return False
        if not all(c in "0123456789abcdef" for c in expected):
            return False

        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest() == expected

    # ----- Dependency resolution -----

    def resolve_dependencies(self, name, installed_db):
        """Resolve dependencies for a package install.

        Returns:
            (success, install_order_or_error)
            install_order is a list of package names in dependency order
        """
        pkg = self.get_package(name)
        if not pkg:
            return False, f"package '{name}' not found"

        to_install = []
        visited = set()
        errors = []

        def resolve(pkg_name):
            if pkg_name in visited:
                return
            visited.add(pkg_name)

            # Already installed?
            if installed_db.get_installed(pkg_name):
                return

            p = self.get_package(pkg_name)
            if not p:
                errors.append(f"dependency '{pkg_name}' not found in any repository")
                return

            # Resolve this package's dependencies first
            for dep in p.get("depends", []):
                resolve(dep)

            to_install.append(pkg_name)

        resolve(name)

        if errors:
            return False, "Unresolved dependencies:\n  " + "\n  ".join(errors)

        return True, to_install


# ---------------------------------------------------------------------------
# Index generation (for repo maintainers / build system)
# ---------------------------------------------------------------------------

def generate_index(package_dir, arch="x86_64", output=None,
                   security_advisories=None):
    """Generate a repository index from a directory of packages.

    Scans package_dir for .igos.tar.gz files, reads their metadata,
    and produces a gzipped JSON index.

    Args:
        package_dir: Directory containing built packages
        arch: Architecture string
        output: Output path for the index (default: package_dir/InterGenOS.db)
        security_advisories: Optional dict mapping "<name>-<version>" keys
            to {"cves": [CVE-ID, ...]} entries. When a package's name +
            version matches a key, the on-wire index entry gets
            `security: true` + `cves: [list]` fields (Q7 / O-030).
            Caller parses the YAML file at docs/governance/security-
            advisories.yml and passes the parsed dict.

    Returns:
        Path to the generated index
    """
    package_dir = Path(package_dir)
    if not output:
        output = package_dir / "InterGenOS.db"

    packages = {}

    for pkg_file in sorted(package_dir.glob("*.igos.tar.gz")):
        # Read package metadata from the archive
        meta = _read_package_meta(pkg_file)
        if meta:
            # Compute SHA256
            sha = hashlib.sha256()
            with open(pkg_file, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha.update(chunk)

            meta["sha256"] = sha.hexdigest()
            meta["size"] = pkg_file.stat().st_size
            meta["filename"] = pkg_file.name

            name = meta.pop("name", pkg_file.stem.split("-")[0])
            packages[name] = meta

    # Q7 (O-030): apply hand-curated security advisories. Match each
    # advisory key <name>-<version> against packages[name]["version"];
    # on hit, stamp security=true + cves=[...]. F-002 (v1.1) replaces
    # this with automated CVE-feed ingestion + per-version severity
    # scoring — the on-wire shape stays the same so future automation
    # is a pure substrate swap.
    advisories = security_advisories or {}
    for adv_key, adv_entry in advisories.items():
        if not isinstance(adv_key, str) or "-" not in adv_key:
            continue
        adv_name, adv_version = adv_key.rsplit("-", 1)
        pkg = packages.get(adv_name)
        if pkg is None or str(pkg.get("version", "")) != adv_version:
            continue
        cves = list((adv_entry or {}).get("cves", []) or [])
        if not cves:
            continue
        pkg["security"] = True
        pkg["cves"] = cves

    # L-019: emit Valid-Until alongside generated. 24h default window
    # bounds the trust horizon for any individual snapshot; mirror
    # operators publish a fresh index daily for routine signing.
    generated_dt = datetime.now(timezone.utc)
    valid_until_dt = generated_dt + timedelta(hours=24)

    index = {
        "version": 1,
        # L-020: signature_format + min_pkm_version envelope fields.
        # Emitting them now keeps generator/parser aligned; current
        # values are the only ones SUPPORTED_SIGNATURE_FORMATS + the
        # active pkm version allow. Field set must coordinate with
        # parser-side SUPPORTED_INDEX_VERSIONS / SUPPORTED_SIGNATURE_FORMATS.
        "signature_format": "gpg-detached-armored",
        "min_pkm_version": "0.1.0",
        "generated": generated_dt.isoformat(),
        # L-019: anti-rollback + Valid-Until pair. The parser-side check
        # requires ISO8601 timestamps + monotonic non-decreasing generated.
        "valid_until": valid_until_dt.isoformat(),
        "arch": arch,
        "package_count": len(packages),
        "packages": packages,
    }

    # Write gzipped JSON
    output = Path(output)
    with gzip.open(output, "wt", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)

    return output


def sign_index(index_path, gpg_key_id=None):
    """Create a GPG detached signature for a repository index.

    Args:
        index_path: Path to InterGenOS.db
        gpg_key_id: GPG key ID to sign with (uses default key if None)

    Returns:
        Path to the signature file
    """
    sig_path = Path(str(index_path) + ".sig")
    cmd = ["gpg", "--detach-sign", "--armor", "--output", str(sig_path)]
    if gpg_key_id:
        cmd.extend(["--local-user", gpg_key_id])
    cmd.append(str(index_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"GPG signing failed: {result.stderr}")

    return sig_path


def _read_package_meta(archive_path):
    """Read metadata from a package archive.

    Looks for .PKGINFO or metadata.json inside the archive.
    """
    try:
        # Try reading .PKGINFO from the archive
        result = subprocess.run(
            ["tar", "-xf", str(archive_path), "-O", ".PKGINFO"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout:
            return _parse_pkginfo(result.stdout)

        # Try metadata.json
        result = subprocess.run(
            ["tar", "-xf", str(archive_path), "-O", "metadata.json"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


def _parse_pkginfo(text):
    """Parse .PKGINFO key=value format."""
    meta = {"depends": []}
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "depend":
            meta["depends"].append(value)
        elif key == "pkgname":
            meta["name"] = value
        elif key == "pkgver":
            meta["version"] = value
        elif key == "pkgdesc":
            meta["description"] = value
        elif key in ("license", "tier", "builddate", "size"):
            if key == "builddate":
                key = "build_date"
            elif key == "size":
                key = "installed_size"
                value = int(value)
            meta[key] = value
    return meta
