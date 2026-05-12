"""Check freedesktop.org mirrors for newer upstream versions.

Pattern: https://www.freedesktop.org/software/<project>/releases/<name>-<version>.tar.xz
Checks /software/<project>/ directory listing for newer tarballs.
"""

import ssl
import urllib.request
from ..base import Candidate, UpstreamChecker
from .gnu_ftp import _version_newer


class FreedesktopChecker(UpstreamChecker):
    def check(self, url_pattern, current_version, name, pkg_meta):
        candidates = []
        try:
            upstream = pkg_meta.get("upstream_check", {})
            project = upstream.get("project", "")
            if not project:
                from urllib.parse import urlparse
                parsed = urlparse(url_pattern)
                parts = parsed.path.strip("/").split("/")
                if len(parts) >= 3 and parts[1] == "software":
                    project = parts[2]

            if not project:
                return candidates

            dir_url = f"https://www.freedesktop.org/software/{project}/"
            ctx = ssl.create_default_context()
            req = urllib.request.Request(dir_url, headers={"User-Agent": "InterGenOS/vps-source-poller"})
            listing = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode("utf-8", errors="replace")

            tarball_exts = {".tar.gz", ".tar.xz", ".tar.bz2"}
            for line in listing.split("\n"):
                if 'href="' not in line:
                    continue
                href = line.split('href="')[1].split('"')[0]
                if name not in href.lower().replace("-", ""):
                    continue
                if not any(href.endswith(ext) for ext in tarball_exts):
                    continue

                base = href.rsplit(".tar", 1)[0]
                try:
                    ver_start = base.index(name) + len(name)
                    if ver_start < len(base) and base[ver_start] in "-_":
                        ver_start += 1
                    ver_str = base[ver_start:]
                    if ver_str and ver_str[0].isdigit() and _version_newer(ver_str, current_version):
                        new_url = f"{dir_url}{href}"
                        candidates.append(Candidate(version=ver_str, url=new_url, source="freedesktop"))
                except (ValueError, IndexError):
                    continue

        except Exception as e:
            print(f"    freedesktop check failed for {name}: {e}", flush=True)

        return candidates
