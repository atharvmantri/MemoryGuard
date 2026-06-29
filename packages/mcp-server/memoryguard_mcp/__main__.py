# SPDX-License-Identifier: Apache-2.0
"""Module entry point: ``python -m memoryguard_mcp``.

Delegates to :func:`memoryguard_mcp.server.main`, which starts the MCP stdio
server over the SQLite store named by ``MEMORYGUARD_STORE`` (default
``memoryguard.db``). When the optional ``mcp`` dependency is absent it prints a
helpful message and exits cleanly rather than raising an import error.
"""

from .server import main

if __name__ == "__main__":  # pragma: no cover
    main()
