# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Console — terminal overlay for the InterGen AI assistant.

Connects as a WebSocket client to web_server.py using the same protocol
as the browser frontend. Uses prompt_toolkit for the REPL shell and Rich
for formatted terminal output.

Start with: intergen console
"""

__version__ = "0.1.0"