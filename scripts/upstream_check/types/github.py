"""Check GitHub releases API for newer upstream versions."""

import json
import ssl
import urllib.request
from ..base import Candidate, UpstreamChecker
from .gnu_ftp import _version_newer


class GitHubChecker(UpstreamChecker):
    def check(self, url_pattern, current_version, name, pkg_meta):
        candidates = []
        try:
            upstream = pkg_meta.get("upstream_check", {})
            repo = upstream.get("repo", "")
            if not repo:
                from urllib.parse import urlparse
                parsed = urlparse(url_pattern)
                path_parts = parsed.path.strip("/").split("/")
                if len(path_parts) >= 2:
                    repo = f"{path_parts[0]}/{path_parts[1]}"
            if not repo:
                return candidates

            api_url = f"https://api.github.com/repos/{repo}/releases?per_page=10"
            ctx = ssl.create_default_context()
            req = urllib.request.Request(api_url, headers={
                "User-Agent": "InterGenOS/vps-source-poller",
                "Accept": "application/vnd.github.v3+json",
            })
            data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=30).read())

            for release in data:
                tag = release.get("tag_name", "")
                ver = tag.lstrip("vV")
                if not ver or not ver[0].isdigit():
                    continue
                if _version_newer(ver, current_version):
                    for asset in release.get("assets", []):
                        asset_url = asset.get("browser_download_url", "")
                        if any(asset_url.endswith(ext) for ext in [".tar.gz", ".tar.xz", ".tar.bz2", ".zip"]):
                            candidates.append(Candidate(version=ver, url=asset_url, source="github"))
                            break

        except Exception as e:
            print(f"    GitHub check failed for {name}: {e}", flush=True)

        return candidates
