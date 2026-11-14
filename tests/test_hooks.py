"""Regression tests for the Codex lifecycle bridge.

These tests keep the Codex-specific glue small and verify that the actual fold still comes from
breadcrumbs-common rather than a second implementation in this adapter.
"""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from breadcrumbs.crumbs import get_crumb
from breadcrumbs.history import Refs
from breadcrumbs.store import Store

from hooks import bridge


ROOT = Path(__file__).resolve().parent.parent


class CodexHooks(unittest.TestCase):
    def env(self, directory, **extra):
        values = {
            "BREADCRUMBS_HOME": str(Path(directory) / "breadcrumbs"),
            "PLUGIN_DATA": str(Path(directory) / "plugin-data"),
        }
        values.update({key: str(value) for key, value in extra.items()})
        return patch.dict(os.environ, values, clear=False)

    def test_hook_manifest_covers_codex_compaction_lifecycle(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        for name in ("SessionStart", "UserPromptSubmit", "PostModelRequest", "PreCompact", "Stop"):
            self.assertIn(name, hooks)
            command = hooks[name][0]["hooks"][0]["command"]
            self.assertEqual(command, 'sh "${PLUGIN_ROOT}/bin/bc-python" "${PLUGIN_ROOT}/hooks/bridge.py"')
        self.assertEqual(hooks["SessionStart"][0]["matcher"], "^compact$")
        self.assertEqual(hooks["SessionStart"][0]["additionalContextLimit"], 4000)
        self.assertEqual(hooks["PreCompact"][0]["matcher"], "auto|manual")

    def test_threshold_tokens_is_positive_and_safe_on_bad_environment(self):
        with patch.dict(os.environ, {"BREADCRUMBS_CODEX_THRESHOLD_TOKENS": "12345"}):
            self.assertEqual(bridge.threshold_tokens(), 12345)
        with patch.dict(os.environ, {"BREADCRUMBS_CODEX_THRESHOLD_TOKENS": "0"}):
            self.assertEqual(bridge.threshold_tokens(), 1)
        with patch.dict(os.environ, {"BREADCRUMBS_CODEX_THRESHOLD_TOKENS": "not-a-number"}):
            self.assertEqual(bridge.threshold_tokens(), 150000)

    def test_post_model_usage_is_saved_but_child_usage_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory, BREADCRUMBS_CODEX_THRESHOLD_TOKENS=1000):
            bridge.monitor_usage({"session_id": "root", "model": "fixture", "turn_id": "t1",
                                  "token_usage": {"input_tokens": 1200}})
            path, _ = bridge.state_paths("root")
            state = bridge.read_state(path)
            self.assertEqual((state["last_input_tokens"], state["threshold_tokens"], state["over_threshold"]),
                             (1200, 1000, True))
            bridge.monitor_usage({"session_id": "root", "agent_id": "child",
                                  "token_usage": {"input_tokens": 1}})
            self.assertEqual(bridge.read_state(path)["last_input_tokens"], 1200)

    def test_stop_ignores_subagent_transcripts(self):
        stdin = io.StringIO(json.dumps({"hook_event_name": "Stop", "agent_id": "child"}))
        stdout = io.StringIO()
        with patch.object(bridge, "ingest", side_effect=AssertionError("child must not be ingested")), \
                patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            self.assertEqual(bridge.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"continue": True})

    def test_non_object_hook_input_is_ignored_without_crashing(self):
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO("[]")), patch("sys.stdout", stdout):
            self.assertEqual(bridge.main(), 0)
        self.assertEqual(stdout.getvalue(), "")

    def test_session_start_consumes_pending_layer_once(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory):
            path, _ = bridge.state_paths("root")
            bridge.write_state(path, {"pending_layer": "supplement", "pending_items": [("deadbeef:0-1", 0.5, 9)],
                                      "fold_session": "root", "fold_turn": 4})
            with patch.object(bridge, "record_fold") as receipt:
                self.assertEqual(bridge.take_pending_layer("root"), "supplement")
                self.assertEqual(bridge.take_pending_layer("root"), "")
            receipt.assert_called_once_with("root", 4, "supplement", [["deadbeef:0-1", 0.5, 9]])
            self.assertNotIn("pending_layer", bridge.read_state(path))

    def test_session_start_hook_emits_additional_context_contract(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory):
            path, _ = bridge.state_paths("root")
            bridge.write_state(path, {"pending_layer": "supplement", "pending_items": [],
                                      "fold_session": "root", "fold_turn": 1})
            stdin = io.StringIO(json.dumps({"hook_event_name": "SessionStart", "source": "compact",
                                             "session_id": "root"}))
            stdout = io.StringIO()
            with patch.object(bridge, "record_fold"), patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                self.assertEqual(bridge.main(), 0)
            output = json.loads(stdout.getvalue())
            self.assertEqual(output, {"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": "supplement"
            }})

    def test_messages_for_fold_keeps_tool_calls_and_results(self):
        rows = [{"role": "user", "content": "inspect the file"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {
                    "name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "content": "file contents", "tool_name": "read_file", "tool_call_id": "c1"},
                {"role": "assistant", "content": "done"}]
        folded = bridge.messages_for_fold(rows)
        self.assertEqual([row["role"] for row in folded], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(folded[1]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual((folded[2]["tool_call_id"], folded[2]["name"]), ("c1", "read_file"))

    def test_precompact_prepares_a_layer_using_shared_engine(self):
        rows = []
        for i in range(8):
            rows.extend(({"role": "user", "content": "Question %d: %s" % (i, "alpha beta gamma " * 70)},
                         {"role": "assistant", "content": "Answer %d: %s" % (i, "delta epsilon zeta " * 70)}))
        with tempfile.TemporaryDirectory() as directory, self.env(directory, BREADCRUMBS_CODEX_FOLD_TOKENS=1000):
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text("fixture\n")
            with patch("breadcrumbs_codex.sessions.session", return_value=("fold-session", "", {}, rows)), \
                    patch("breadcrumbs_codex.sessions.requests", return_value=[]):
                bridge.prepare_fold({"session_id": "fold-session", "transcript_path": str(transcript),
                                     "trigger": "auto", "turn_id": "turn-8"})
            path, _ = bridge.state_paths("fold-session")
            state = bridge.read_state(path)
            self.assertIn("pending_layer", state)
            self.assertIn("Codex supplement", state["pending_layer"])
            self.assertEqual(state["fold_trigger"], "auto")
            self.assertTrue(state["pending_items"])

    def test_precompact_clears_stale_layer_when_transcript_has_no_rows(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory):
            transcript = Path(directory) / "empty-session.jsonl"
            transcript.write_text("fixture\n")
            path, _ = bridge.state_paths("empty-session")
            bridge.write_state(path, {"pending_layer": "stale", "pending_items": [["deadbeef:0-1", 0.5, 4]]})
            with patch("breadcrumbs_codex.sessions.session", return_value=("empty-session", "", {}, [])), \
                    patch("breadcrumbs_codex.sessions.requests", return_value=[]):
                bridge.prepare_fold({"session_id": "empty-session", "transcript_path": str(transcript),
                                     "trigger": "auto"})
            state = bridge.read_state(path)
            self.assertNotIn("pending_layer", state)
            self.assertNotIn("pending_items", state)

    def test_precompact_ignores_subagent_contexts(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory):
            transcript = Path(directory) / "child-session.jsonl"
            transcript.write_text("fixture\n")
            with patch.object(bridge, "state_paths", side_effect=AssertionError("child state must not be touched")):
                bridge.prepare_fold({"session_id": "child", "transcript_path": str(transcript),
                                     "trigger": "auto", "agent_id": "child-agent"})

    def test_fold_receipt_has_valid_source_ranges_and_ref(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory):
            bridge.record_fold("receipt-session", 3, "supplement\n", [("deadbeef:0-1", 0.5, 12)])
            refs = Refs(str(Path(directory) / "breadcrumbs" / "refs"), readonly=True)
            receipt_id = refs.get("rcpt-receipt-session")
            self.assertIsNotNone(receipt_id)
            store = Store(str(Path(directory) / "breadcrumbs" / "store"), readonly=True)
            try:
                receipt = get_crumb(store, receipt_id)
            finally:
                store.close()
            self.assertEqual((receipt.kind, receipt.meta["sess"], receipt.meta["turn"]),
                             ("rcpt", "receipt-session", 3))
            self.assertEqual(receipt.ranges[0], ["deadbeef", 0, 1])
            self.assertEqual(receipt.meta["ctx"], receipt.ranges[-1][0])

    def test_fold_receipt_discards_one_bad_item_but_keeps_valid_provenance(self):
        with tempfile.TemporaryDirectory() as directory, self.env(directory):
            bridge.record_fold("mixed-session", 2, "supplement\n", [
                ["not-a-source-ref", 0.5, 4], ["deadbeef:0-1", 0.25, 8], ["deadbeef:broken", 1.0, 3]
            ])
            refs = Refs(str(Path(directory) / "breadcrumbs" / "refs"), readonly=True)
            receipt_id = refs.get("rcpt-mixed-session")
            store = Store(str(Path(directory) / "breadcrumbs" / "store"), readonly=True)
            try:
                receipt = get_crumb(store, receipt_id)
            finally:
                store.close()
            self.assertEqual(receipt.meta["items"], ["deadbeef:0-1"])
            self.assertEqual(receipt.meta["lv"], [0.25])
            self.assertEqual(receipt.meta["b"], [8])


if __name__ == "__main__":
    unittest.main()
