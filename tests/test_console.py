"""The console of the explorer reaches the Codex settings through the harness table, with the signature of the port."""
import os
import tempfile
import unittest
from unittest.mock import patch

from breadcrumbs import ports


class Console(unittest.TestCase):
    def test_the_codex_entry_answers_settings_with_root_action_payload(self):
        import breadcrumbs_codex.adapter  # noqa: F401  registers the harness
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"CODEX_HOME": home}):
            settings = ports.harnesses()["codex"]["settings"]
            got = settings(home, "get", {})
            self.assertEqual([f["key"] for f in got["fields"]],
                             ["native_compaction", "compact_limit", "threshold_tokens", "fold_tokens"])
            self.assertFalse(settings(home, "set", {"key": "native_compaction", "value": True})["fields"][0]["value"] is False)

    def test_the_explorer_view_reads_and_writes_them(self):
        try:
            from breadcrumbs_spacegraph import view
        except ImportError:
            self.skipTest("breadcrumbs-spacegraph is not on the path")
        import breadcrumbs_codex.adapter  # noqa: F401
        from breadcrumbs.history import Refs, commit
        from breadcrumbs.index import Index
        from breadcrumbs.ingest import put_tree, put_turn
        from breadcrumbs.store import Store
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root, \
                patch.dict(os.environ, {"CODEX_HOME": home}):
            store, index, refs = Store(os.path.join(root, "store")), Index(os.path.join(root, "index.db")), Refs(os.path.join(root, "refs"))
            turns = [put_turn(store, index, "user", "the kettle boils too fast %d" % i, "s1", ts=i) for i in range(2)]
            commit(store, refs, "s1", [put_tree(store, "sess", "tea", turns)], "s1", ts=3)
            index.commit()
            index.close()
            store.close()
            current = view.View(root)
            self.addCleanup(current.close)
            got = current.settings("get", {"harness": "codex"})
            self.assertEqual(got["harness_key"], "codex")
            self.assertIn("native_compaction", [f["key"] for f in got["harness"]["fields"]])
            current.settings("set", {"target": "harness", "harness": "codex", "key": "threshold_tokens", "value": 90000})
            current.settings("set", {"target": "common", "harness": "codex", "key": "ace_ratchet", "value": 2.0})
            again = current.settings("get", {"harness": "codex"})
            self.assertEqual(next(f["value"] for f in again["harness"]["fields"] if f["key"] == "threshold_tokens"), 90000)
            rows = {r["key"]: r["value"] for g in again["common"]["groups"] for r in g["rows"]}
            self.assertEqual(rows["ace_ratchet"], 2.0)


if __name__ == "__main__":
    unittest.main()
