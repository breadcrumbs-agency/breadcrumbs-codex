"""The adapter of the package breadcrumbs_codex: Codex gives its entry of the harness table of core.py.
ports.load() imports this module by the name of its package (breadcrumbs_*); no module imports it.
"""
import shlex

from breadcrumbs import ports

if ports.layer("codex", __file__):
    ports.harness("codex", "Codex", ("codex",), lambda cmd: "codex mcp add breadcrumbs -- " + shlex.join(cmd), order=30)
