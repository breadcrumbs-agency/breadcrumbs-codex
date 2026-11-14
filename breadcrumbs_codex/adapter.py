"""The adapter of the package breadcrumbs_codex: Codex gives its entry of the harness table of core.py.
ports.load() imports this module by the name of its package (breadcrumbs_*); no module imports it.

The Codex icon travels with this package so the breadcrumbs explorer can identify Codex views beside its brand.
"""
from breadcrumbs import ports


def _source(path):
    from . import sessions
    return sessions.sessions(path)


def _usage(path):
    from . import sessions
    return sessions.usage(path)


def _settings(root, action, payload):
    from .managed import Managed
    return Managed().harness_settings(action, payload)


if ports.layer("codex", __file__):
    ports.harness("codex", "Codex", ("codex",), ports.mcp_add("codex"), order=30, source=_source,
                  usage=_usage, icon=ports.image(__file__, "icon.png"), settings=_settings,
                  repository="https://github.com/breadcrumbs-agency/breadcrumbs-codex",
                  pages="https://breadcrumbs-agency.github.io/hermes-breadcrumb-plugin/")
