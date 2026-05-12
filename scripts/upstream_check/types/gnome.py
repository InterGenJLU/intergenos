"""Check GNOME download servers for newer upstream versions."""

import ssl
import urllib.request
from ..base import Candidate, UpstreamChecker
from .gnu_ftp import _version_newer


class GnomeChecker(UpstreamChecker):
    def check(self, url_pattern, current_version, name, pkg_meta):
        candidates = []
        try:
            series_dir = url_pattern[:url_pattern.rindex("/")]
            ctx = ssl.create_default_context()
            req = urllib.request.Request(series_dir + "/", headers={"User-Agent": "InterGenOS/vps-source-poller"})
            listing = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode("utf-8", errors="replace")

            tarball_exts = {".tar.gz", ".tar.xz", ".tar.bz2"}
            for line in listing.split("\n"):
                if 'href="' not in line:
                    continue
                href = line.split('href="')[1].split('"')[0]
                if name not in href:
                    continue
                if not any(href.endswith(ext) for ext in tarball_exts):
                    continue

                ver = href.replace(name + "-", "").rsplit(".tar", 1)[0]
                if ver and ver[0].isdigit() and _version_newer(ver, current_version):
                    new_url = f"{series_dir}/{href}"
                    candidates.append(Candidate(version=ver, url=new_url, source="gnome"))

        except Exception as e:
            print(f"    GNOME check failed for {name}: {e}", flush=True)

        return candidates
