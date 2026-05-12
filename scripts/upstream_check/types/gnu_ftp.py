"""Check GNU FTP mirrors for newer upstream versions."""

import re
import ssl
import urllib.request
from ..base import Candidate, UpstreamChecker


class GnuFtpChecker(UpstreamChecker):
    def check(self, url_pattern, current_version, name, pkg_meta):
        candidates = []
        try:
            dir_url = url_pattern[:url_pattern.rindex("/")]
            ctx = ssl.create_default_context()
            req = urllib.request.Request(dir_url + "/", headers={"User-Agent": "InterGenOS/vps-source-poller"})
            listing = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode("utf-8", errors="replace")

            tarball_exts = {".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".tar.lz"}
            versions_found = set()

            for line in listing.split("\n"):
                if 'href="' not in line:
                    continue
                href = line.split('href="')[1].split('"')[0]
                if not any(href.endswith(ext) for ext in tarball_exts):
                    continue

                base = href.rsplit(".tar", 1)[0]
                try:
                    ver_start = base.index(name) + len(name)
                    if ver_start < len(base) and base[ver_start] in "-_":
                        ver_start += 1
                    ver_str = base[ver_start:]
                    if ver_str and ver_str[0].isdigit():
                        versions_found.add(ver_str)
                except (ValueError, IndexError):
                    continue

            for ver in sorted(versions_found):
                if _version_newer(ver, current_version):
                    new_url = url_pattern.replace("${version}", ver)
                    candidates.append(Candidate(version=ver, url=new_url, source="gnu-ftp"))

        except Exception as e:
            print(f"    GNU-FTP check failed for {name}: {e}", flush=True)

        return candidates


def _parse_version(v):
    parts = []
    for segment in re.split(r"[.-]", v):
        try:
            parts.append(int(segment))
        except ValueError:
            for char in segment:
                if char.isdigit():
                    parts.append(ord(char))
                else:
                    parts.append(ord(char))
    return tuple(parts)


def _version_newer(candidate, current):
    try:
        return _parse_version(candidate) > _parse_version(current)
    except Exception:
        return False
