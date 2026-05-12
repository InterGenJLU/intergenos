"""Check crates.io cargo registry for newer upstream versions."""

import json
import ssl
import urllib.request
from ..base import Candidate, UpstreamChecker
from .gnu_ftp import _version_newer


class CargoChecker(UpstreamChecker):
    def check(self, url_pattern, current_version, name, pkg_meta):
        candidates = []
        try:
            upstream = pkg_meta.get("upstream_check", {})
            crate_name = upstream.get("crate_name", name)
            api_url = f"https://crates.io/api/v1/crates/{crate_name}"
            ctx = ssl.create_default_context()
            req = urllib.request.Request(api_url, headers={"User-Agent": "InterGenOS/vps-source-poller"})
            data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=30).read())

            latest = data.get("crate", {}).get("max_stable_version")
            if not latest:
                latest = data.get("crate", {}).get("newest_version", "")

            if latest and _version_newer(latest, current_version):
                candidates.append(Candidate(
                    version=latest,
                    url=f"https://crates.io/api/v1/crates/{crate_name}/{latest}/download",
                    source="cargo",
                ))

        except Exception as e:
            print(f"    Cargo check failed for {name}: {e}", flush=True)

        return candidates
