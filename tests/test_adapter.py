"""The adapter of breadcrumbs_codex: it gives the entry 'codex' of the harness table of core.py (identify and setup).
Run with breadcrumbs-common and this folder on PYTHONPATH."""
import json
import unittest

from breadcrumbs import core, ports


class Adapter(unittest.TestCase):
    CMD = ["env", "PYTHONPATH=/x", "python", "-m", "breadcrumbs", "core", "mcp"]

    def setUp(self):
        self.entry = ports.harnesses()['codex']

    def test_the_package_gives_one_entry_to_the_table(self):
        self.assertEqual(self.entry["label"], 'Codex')
        self.assertEqual(ports.layer("codex", __file__), False)  # the adapter announced its layer once

    def test_identify_tells_the_client_names_apart(self):
        for name in ['codex-mcp-client', 'codex']:
            self.assertEqual(core.identify(name), 'codex', name)
        for name in ("curl", "", None):
            self.assertEqual(core.identify(name), "unknown", name)

    def test_the_table_lists_it_in_its_place(self):
        self.assertIn('codex', core.harnesses())

    def test_setup_is_the_mcp_add_line_of_the_cli(self):
        self.assertEqual(self.entry["setup"](self.CMD), 'codex mcp add breadcrumbs -- env PYTHONPATH=/x python -m breadcrumbs core mcp')

    def test_health_counts_the_calls_of_this_harness(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            c = core.Core(core.Stores(root=tmp))
            try:
                c.call("stores", harness='codex')
                self.assertEqual(c.call("health")["calls"], {'codex': 1, "unknown": 1})
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
