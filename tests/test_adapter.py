"""The adapter of breadcrumbs_codex: it gives the entry 'codex' of the harness table of core.py (identify and setup).
Run with breadcrumbs-common and this folder on PYTHONPATH."""
import unittest

from breadcrumbs import harness_case


class Adapter(harness_case.HarnessCase):
    KEY, LABEL, LAYER = "codex", "Codex", "codex"
    NAMES = ['codex-mcp-client', 'codex']
    SETUP = "codex mcp add breadcrumbs -- env PYTHONPATH=/x python -m breadcrumbs core mcp"


if __name__ == "__main__":
    unittest.main()
