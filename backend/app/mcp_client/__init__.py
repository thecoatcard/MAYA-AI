"""MCP client adapter (ADR-0028): MAYA uses third-party MCP servers behind its own gate.

Off by default (``MAYA_MCP_CLIENT_ENABLED``). The ``mcp`` SDK is imported only when a server is
actually started (``runner.py``), so with the switch off nothing here imports or spawns it.
"""
