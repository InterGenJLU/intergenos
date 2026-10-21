# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A payload download must not import Python modules from its working directory."""
import hashlib
import http.server
import os
from pathlib import Path
import subprocess
import threading

import pytest

LIBRARY = Path(__file__).resolve().parents[2] / "packages/core/intergenos-helper-lib/helper-lib.sh"


@pytest.mark.parametrize("transport", ["wget", "curl"])
@pytest.mark.parametrize("planted", [False, True])
def test_download_ignores_working_directory_modules(tmp_path, transport, planted):
    payload = bytes(range(256)) * 256
    marker = tmp_path / "imported"
    if planted:
        # Record the import, then provide the real module so a download
        # failure cannot be mistaken for successful import isolation.
        (tmp_path / "selectors.py").write_text(
            "import os, sys, importlib\n"
            "open('imported', 'w').close()\n"
            "name = __name__\n"
            "del sys.modules[name]\n"
            "sys.path[:] = [p for p in sys.path if p not in ('', os.getcwd())]\n"
            "sys.modules[name] = importlib.import_module(name)\n")

    class Source(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/payload"
            destination = tmp_path / "payload"
            proc = subprocess.run(
                ["/bin/bash", "-c",
                 'source "$1"; igos_helper_download "$2" "$3" "$4"',
                 "download-test", str(LIBRARY), str(destination), url, transport],
                cwd=tmp_path, env=dict(os.environ, PATH="/usr/bin:/bin"),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        finally:
            server.shutdown()
            thread.join()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256(payload).digest()
    assert "done" in proc.stdout
    assert not marker.exists(), "the download imported selectors.py from its working directory"
