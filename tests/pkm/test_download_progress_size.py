# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The response boundary retains the index size when HTTP supplies no length."""
import hashlib
import http.server
import io
from pathlib import Path
import threading

import pytest

from pkm import progress, repo
from pkm.output import NORMAL, Reporter


@pytest.mark.parametrize("index_size,declared_length", [(1048576, False), (None, False), (1, True)])
def test_download_progress_uses_an_available_size(tmp_path, monkeypatch, index_size, declared_length):
    payload = bytes(range(256)) * 4096

    class Source(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if declared_length:
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    monkeypatch.setattr(repo, "_TRACE_AVAILABLE", False)
    monkeypatch.setattr(progress.ByteProgress, "LOG_INTERVAL", 0)
    # Emit each byte update but do not create a zero-interval heartbeat.
    monkeypatch.setattr(progress.ByteProgress, "start", lambda self: self.advance(0))
    stream = io.StringIO()
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            manager = repo.RepoManager.__new__(repo.RepoManager)
            manager.repos = {}
            manager.cache_dir = lambda: tmp_path
            manager.pkg_cache = lambda: tmp_path
            manager.get_package = lambda name: {
                "name": name, "version": "1.0", "release": 1, "filename": "payload",
                "repo": f"http://127.0.0.1:{server.server_port}",
                "sha256": hashlib.sha256(payload).hexdigest(), "size": index_size,
            }
            ok, result = manager.download_package("progress-test", reporter=Reporter(level=NORMAL, stream=stream))
        finally:
            server.shutdown()
            thread.join()
    assert ok, result
    assert Path(result).read_bytes() == payload
    lines = [line for line in stream.getvalue().splitlines() if "elapsed " in line]
    assert len(lines) > 2, stream.getvalue()
    if index_size is None and not declared_length:
        assert all("total unknown" in line and "%" not in line for line in lines)
    else:
        assert all("%" in line and "total unknown" not in line for line in lines)
        assert "100.0%" in lines[-1]
        assert "of 1.0 MiB" in lines[-1]
