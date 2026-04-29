"""pkm repo layer — Remote repository sync, package fetching, and verification.

Repository Structure:
    https://repo.intergenos.org/
    ├── x86_64/
    │   ├── InterGenOS.db          # Package index (JSON, gzipped)
    │   ├── InterGenOS.db.sig      # GPG detached signature of the index
    │   ├── firefox-138.0-1.igos.tar.gz
    │   ├── gimp-2.10.38-1.igos.tar.gz
    │   └── ...
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
                "source_url": "https://archive.mozilla.org/pub/firefox/releases/138.0/source/firefox-138.0.source.tar.xz"
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

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from .database import PackageDB


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_CONFIG_PATH = Path("/etc/pkm/repos.conf")
REPO_CACHE_DIR = Path("/var/cache/pkm")
REPO_DB_CACHE = REPO_CACHE_DIR / "db"
REPO_PKG_CACHE = REPO_CACHE_DIR / "packages"
GPG_KEYRING = Path("/etc/pkm/trusted.gpg")

DEFAULT_REPOS = {
    "intergenos": {
        "url": "https://repo.intergenos.org/x86_64",
        "enabled": True,
        "priority": 100,
    }
}

# Max age before forcing a re-sync (seconds)
INDEX_MAX_AGE = 3600  # 1 hour


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

        # Ensure cache dirs exist
        REPO_DB_CACHE.mkdir(parents=True, exist_ok=True)
        REPO_PKG_CACHE.mkdir(parents=True, exist_ok=True)

    def _load_repos(self):
        """Load repository configuration."""
        if REPO_CONFIG_PATH.exists():
            try:
                with open(REPO_CONFIG_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return DEFAULT_REPOS

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

            except urllib.error.URLError as e:
                results.append((name, False, f"network error: {e.reason}"))
                db_path.unlink(missing_ok=True)
                sig_path.unlink(missing_ok=True)
            except Exception as e:
                results.append((name, False, f"error: {e}"))
                db_path.unlink(missing_ok=True)
                sig_path.unlink(missing_ok=True)

        return results

    def _download(self, url, dest):
        """Download a file with progress indication."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "pkm/1.0 (InterGenOS)"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)

    def _verify_signature(self, data_path, sig_path):
        """Verify GPG detached signature."""
        result = subprocess.run(
            ["gpg", "--no-default-keyring", "--keyring", str(GPG_KEYRING),
             "--verify", str(sig_path), str(data_path)],
            capture_output=True, text=True
        )
        return result.returncode == 0

    def _parse_index(self, name, url, db_path):
        """Parse a gzipped JSON repository index."""
        with gzip.open(db_path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        return RepoIndex(name, url, data)

    # ----- Query -----

    def search(self, term):
        """Search across all synced repos."""
        self._ensure_synced()
        results = []
        for index in self.indexes.values():
            results.extend(index.search(term))
        # Deduplicate — higher priority repo wins
        seen = {}
        for r in results:
            if r["name"] not in seen:
                seen[r["name"]] = r
        return list(seen.values())

    def get_package(self, name):
        """Find a package across all repos."""
        self._ensure_synced()
        for index in self.indexes.values():
            pkg = index.get_package(name)
            if pkg:
                return pkg
        return None

    def list_available(self, tier=None):
        """List all available packages, optionally filtered by tier."""
        self._ensure_synced()
        results = []
        seen = set()
        for index in self.indexes.values():
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

        Returns:
            (success, local_path_or_error_message)
        """
        pkg = self.get_package(name)
        if not pkg:
            return False, f"package '{name}' not found in any repository"

        filename = pkg.get("filename")
        if not filename:
            filename = f"{name}-{pkg['version']}-{pkg.get('release', 1)}.igos.tar.gz"

        url = f"{pkg['repo']}/{filename}" if "://" in pkg.get("repo", "") else \
              f"{self.repos[pkg['repo']]['url'].rstrip('/')}/{filename}"

        local_path = REPO_PKG_CACHE / filename

        # Use cached if checksum matches
        if local_path.exists():
            if self._verify_checksum(local_path, pkg.get("sha256")):
                return True, str(local_path)
            else:
                local_path.unlink()  # Stale/corrupt cache

        try:
            self._download(url, local_path)
        except Exception as e:
            return False, f"download failed: {e}"

        # Verify checksum
        expected = pkg.get("sha256")
        if expected and not self._verify_checksum(local_path, expected):
            local_path.unlink(missing_ok=True)
            return False, f"SHA256 verification FAILED for {filename}"

        return True, str(local_path)

    def _verify_checksum(self, path, expected):
        """Verify SHA256 checksum of a file."""
        if not expected:
            return True  # No checksum to verify

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

def generate_index(package_dir, arch="x86_64", output=None):
    """Generate a repository index from a directory of packages.

    Scans package_dir for .igos.tar.gz files, reads their metadata,
    and produces a gzipped JSON index.

    Args:
        package_dir: Directory containing built packages
        arch: Architecture string
        output: Output path for the index (default: package_dir/InterGenOS.db)

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

    index = {
        "version": 1,
        "generated": datetime.now(timezone.utc).isoformat(),
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
