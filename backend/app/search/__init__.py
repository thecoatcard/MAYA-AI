"""Native web search for MAYA: provider chain, page reading and answer-ready results.

Kept free of imports on purpose, so ``app.core.config`` can use ``app.search.names`` without pulling
in the HTTP stack. See ``docs/architecture/WEB_SEARCH.md``.
"""
