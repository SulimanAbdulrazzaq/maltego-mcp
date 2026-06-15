"""maltego_mcp - an MCP server for driving Maltego CE investigation graphs.

Maltego Community Edition exposes no live API, so this package treats Maltego's
native ``.mtgx`` graph file (a ZIP archive of GraphML XML) as the integration
surface. An LLM can build, read, and edit an investigation graph in memory and
then save it as a ``.mtgx`` file the user opens (or refreshes) inside Maltego CE.

A pluggable transform-provider layer (:mod:`maltego_mcp.transforms`) keeps the
design extensible so that a real Maltego API or third-party OSINT services can
be added later without reworking the core.
"""

__version__ = "0.3.0"
