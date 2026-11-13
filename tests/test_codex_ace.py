"""The Codex request adapter runs the ACE engine of breadcrumbs-common at its default target."""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from textwrap import dedent
import threading
import unittest
from unittest.mock import patch

from breadcrumbs.ace_core import request_bytes
from breadcrumbs_spacegraph.view import ace_requests
from breadcrumbs_codex.provider import Conversations, Server
from breadcrumbs_codex.responses import messages


def user(value):
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": value}]}


class CodexAce(unittest.TestCase):
    def test_responses_lite_usage_calibrates_the_next_request_without_content_type(self):
        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'event: response.completed\n'
                                 b'data: {"type":"response.completed","response":{"usage":{"input_tokens":16000}}}\n\n')

            def log_message(self, *_):
                pass

        body = json.dumps({"model": "fixture-model", "stream": True, "prompt_cache_key": "test-session",
                           "input": [{"type": "additional_tools", "role": "developer", "tools": []},
                                     {"type": "message", "role": "developer",
                                      "content": "<tools>\nDeferred tool namespaces:\n- mcp__breadcrumbs\n</tools>"},
                                     user("A request with deferred tools.")]})
        with tempfile.TemporaryDirectory() as directory, ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as upstream:
            upstream_url = f"http://127.0.0.1:{upstream.server_port}"
            with Server(0, upstream_url, "fixture", directory) as ace:
                for server in (upstream, ace):
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    for _ in range(2):
                        connection = HTTPConnection("127.0.0.1", ace.server_port, timeout=4)
                        try:
                            connection.request("POST", "/v1/responses", body, {"Authorization": "Bearer fixture"})
                            with connection.getresponse() as reply:
                                self.assertEqual(reply.status, 200)
                                self.assertIn(b"response.completed", reply.read())
                        finally:
                            connection.close()
                    with ace.conversations.use("test-session", "fixture-model", "") as chat:
                        self.assertEqual(chat.engine.last_prompt_tokens, 16000)
                    self.assertIn("last=16000", (Path(directory) / "ace.log").read_text())
                finally:
                    ace.shutdown()
                    upstream.shutdown()

    def test_folded_requests_match_across_python_hash_seeds(self):
        replay = dedent("""\
            import hashlib
            import json
            import tempfile

            from breadcrumbs.ace_core import request_bytes
            from breadcrumbs_codex.provider import Conversations
            from breadcrumbs_codex.responses import messages

            words = ("turbine coolant relay bearing current pump torque motor pressure flow valve "
                     "rotor voltage temperature gasket screw bracket solar alignment magnetometer")
            history = []
            with tempfile.TemporaryDirectory() as directory:
                conversations = Conversations(directory, context_window=0)
                try:
                    with conversations.use("same-session", "fixture-model", "0") as chat:
                        for i in range(16):
                            history.append({"type": "message", "role": "user", "content":
                                            f"Turn {i}.\\n" + (words + "\\n") * 500})
                            body = {"model": "fixture-model", "input": history}
                            shaped = chat.shape(body, messages(body))
                            count = round(request_bytes(messages(shaped)) * 0.27 + 2800)
                            chat.observe({"input_tokens": count, "output_tokens": 8, "total_tokens": count + 8})
                            print(hashlib.sha256(json.dumps(shaped).encode()).hexdigest())
                        assert chat.engine.folds >= 2
                finally:
                    conversations.close()
            """)
        root = Path(__file__).resolve().parent.parent
        def run(seed):
            env = dict(os.environ, PYTHONHASHSEED=str(seed), BREADCRUMBS_ACE_TOKENS="",
                       PYTHONPATH=os.pathsep.join(p for p in sys.path if p))  # the layers this suite runs with
            return subprocess.check_output([sys.executable, "-c", replay], cwd=root, env=env, text=True,
                                           timeout=45).splitlines()

        first = run(1)
        self.assertEqual(len(first), 16)
        self.assertEqual(first, run(2))
        self.assertEqual(first, run(17))

    def test_default_target_holds_across_multiple_folds(self):
        words = ("turbine coolant relay bearing current pump torque motor pressure flow valve rotor voltage "
                 "temperature gasket screw bracket solar assembly alignment magnetometer impulse bus winding "
                 "switch fixture insulation shroud trace load tolerance actuator differential").split()
        randomizer = random.Random(4259)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"BREADCRUMBS_ACE_TOKENS": ""}):
            conversations = Conversations(directory, context_window=0)
            try:
                history, layers, counts = [], [], []
                with conversations.use("codex-ace-fixture", "fixture-model", "0") as conversation:
                    for turn in range(16):
                        passage = "\n".join(f"{turn:02d}/{line:04d} " + " ".join(randomizer.choices(words, k=25))
                                            for line in range(390))
                        history.append(user(passage))
                        body = {"instructions": "Retain source provenance.", "input": history}
                        shaped = conversation.shape(body, messages(body))
                        count = round(request_bytes(messages(shaped)) * 0.27 + 2800)
                        conversation.observe({"input_tokens": count, "output_tokens": 8, "total_tokens": count + 8})
                        layers.append(conversation.engine.layer)
                        counts.append(count)
                rows = ace_requests(Path(directory))
                self.assertTrue(all(row["target"] == 200000 for row in rows))
                self.assertEqual(rows[0]["act"], "pass")
                first = next(i for i, row in enumerate(rows) if row["act"] == "fold")
                self.assertGreater(rows[first]["raw"], 200000)
                self.assertGreater(rows[-1]["raw"], 330000)
                self.assertLess(max(counts[first:]), 200000)
                self.assertGreaterEqual(sum(row["act"] == "fold" for row in rows), 2)
                self.assertTrue(any(left and left == right for left, right in zip(layers, layers[1:])))
            finally:
                conversations.close()

    def test_parallel_calls_share_a_cut_boundary(self):
        def call(key):
            return {"type": "function_call", "name": "read_file", "call_id": key, "arguments": "{}"}

        def output(key):
            return {"type": "function_call_output", "call_id": key, "output": "file content"}

        items = [user("Look up both files."), call("a"), output("a"), call("b"), output("b"),
                 {"type": "message", "role": "assistant", "content": "Read both."}]
        rows = messages({"input": items})
        self.assertEqual([row["role"] for row in rows], ["system", "user", "assistant", "tool",
                                                        "assistant", "tool", "assistant"])
        items[2:4] = [items[3], items[2]]
        rows = messages({"input": items})
        self.assertEqual(rows[2]["wire"], items[1:3])
        self.assertEqual([row["role"] for row in rows], ["system", "user", "assistant", "tool", "tool", "assistant"])
