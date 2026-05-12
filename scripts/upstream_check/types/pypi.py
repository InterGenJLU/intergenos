"""Check PyPI JSON API for newer upstream versions."""

import json
import ssl
import urllib.request
from ..base import Candidate, UpstreamChecker
from .gnu_ftp import _version_newer


class PyPIChecker(UpstreamChecker):
    def check(self, url_pattern, current_version, name, pkg_meta):
        candidates = []
        try:
            upstream = pkg_meta.get("upstream_check", {})
            pypi_name = upstream.get("pypi_name", name)
            api_url = f"https://pypi.org/pypi/{pypi_name}/json"
            ctx = ssl.create_default_context()
            req = urllib.request.Request(api_url, headers={"User-Agent": "InterGenOS/vps-source-poller"})
            data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=30).read())

            latest = data.get("info", {}).get("version", "")
            if latest and _version_newer(latest, current_version):
                for release in data.get("releases", {}).get(latest, []):
                    if release.get("packagetype") == "sdist":
                        candidates.append(Candidate(version=latest, url=release["url"], source="pypi"))
                        break

        except Exception as e:
            print(f"    PyPI check failed for {name}: {e}", flush=True)

        return candidates
