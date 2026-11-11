"""The adapter of the package breadcrumbs_codex: Codex gives its entry of the harness table of core.py.
ports.load() imports this module by the name of its package (breadcrumbs_*); no module imports it.
"""
from breadcrumbs import ports


def _source(path):
    from . import sessions
    return sessions.sessions(path)


if ports.layer("codex", __file__):
    ports.harness("codex", "Codex", ("codex",), ports.mcp_add("codex"), order=30, source=_source)
