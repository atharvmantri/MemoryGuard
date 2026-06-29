# SPDX-License-Identifier: Apache-2.0
"""MemoryGuard OSS MCP server.

Wraps the core engine and exposes MCP tools (memory_search, memory_add,
memory_explain) and resources, applying the trust/scope/policy filter on search.

The tool/resource handler functions are importable directly (they take an
explicit ``engine`` argument) so they can be unit-tested without a running MCP
transport. The optional ``mcp`` dependency is imported lazily inside
:func:`~memoryguard_mcp.server.build_mcp_server` / :func:`~memoryguard_mcp.server.main`,
so importing this package never hard-fails when ``mcp`` is absent.
"""

from .server import (
    DEFAULT_MIN_TRUST,
    build_mcp_server,
    main,
    resource_memory,
    resource_project_memories,
    tool_memory_add,
    tool_memory_explain,
    tool_memory_search,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MIN_TRUST",
    "tool_memory_search",
    "tool_memory_add",
    "tool_memory_explain",
    "resource_project_memories",
    "resource_memory",
    "build_mcp_server",
    "main",
]
