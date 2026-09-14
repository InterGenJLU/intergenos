"""Keep package command tests away from installed transaction handlers."""

import atexit
import os
import shutil
import tempfile


# An environment override also covers fresh imports and child processes. The
# command paths resolve it at runtime, including commands with an install root.
_PRETXN_TMP = tempfile.mkdtemp(prefix="pkm-pytest-pretxn-")
atexit.register(lambda: shutil.rmtree(_PRETXN_TMP, ignore_errors=True))
os.environ["PKM_PRETXN_HANDLER_DIR"] = _PRETXN_TMP
