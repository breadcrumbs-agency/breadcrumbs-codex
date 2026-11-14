"""The sessions of Codex as a source of the ingest. The fixture is a hand-written file with invented text.
Run with breadcrumbs-common and this folder on PYTHONPATH."""
import datetime
import json
import os
import unittest

from breadcrumbs import harness_case, ports

STAMP = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


def line(kind, payload, **more):
    return json.dumps(dict({"timestamp": "2026-01-02T03:04:05.000Z", "type": kind, "payload": payload}, **more))


def tokens(inp, cached, write, out, rsn, total):
    """A token_count event (made-up counts): the last request, and the total of the session so far."""
    last = {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": write, "output_tokens": out,
            "reasoning_output_tokens": rsn, "total_tokens": inp + out}
    return line("event_msg", {"type": "token_count", "info": {"last_token_usage": last,
                                                             "total_token_usage": dict(last, total_tokens=total)}})


def message(role, text, **more):
    return line("response_item", {"type": "message", "role": role,
                                  "content": [{"type": "input_text" if role != "assistant" else "output_text", "text": text}]}, **more)


class Sessions(harness_case.SourceCase):
    KEY = "codex"

    def fixture(self, folder):
        day = os.path.join(folder, "2026", "01", "02")
        os.makedirs(day)
        with open(os.path.join(day, "rollout-2026-01-02T03-04-05-abc123.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join([
                line("session_meta", {"id": "abc123", "cwd": "/work/demo", "git": {"branch": "main"}, "model_provider": "openai"}),
                line("turn_context", {"model": "gpt-demo"}),
                message("developer", "rules that Codex wrote"),
                message("user", "how long to boil an egg?"),
                line("response_item", {"type": "reasoning", "summary": []}),
                message("assistant", "Let me look at the clock."),
                line("response_item", {"type": "function_call", "name": "shell", "arguments": "{\"cmd\": \"date\"}", "call_id": "c1"}),
                line("response_item", {"type": "function_call_output", "call_id": "c1", "output": "Fri 03:04"}),
                line("response_item", {"type": "custom_tool_call", "name": "exec", "input": "date +%s", "call_id": "c2"}),
                line("response_item", {"type": "custom_tool_call_output", "call_id": "c2", "output": [{"type": "input_text", "text": "1767323045"}]}),
                message("user", "a message that a fork copied from its parent", metadata={"inherited_user_message": True}),
                line("event_msg", {"type": "token_count"}),
                tokens(1000, 800, 0, 50, 10, 1050),
                tokens(1000, 800, 0, 50, 10, 1050),  # Codex writes the same event again: one request
                tokens(1200, 900, 50, 30, 0, 2280),
                "{this line is broken",
                message("assistant", "About ten minutes."),
            ]) + "\n")
        return folder

    def test_a_fixture_becomes_turns_and_tool_rows(self):
        (sid, _title, _meta, rows), = self.sessions()
        self.assertEqual(sid, "abc123")
        self.assertEqual([(r["role"], r["content"]) for r in rows], [
            ("user", "how long to boil an egg?"), ("assistant", "Let me look at the clock."), ("tool", "Fri 03:04"),
            ("assistant", ""), ("tool", "1767323045"), ("assistant", "About ten minutes.")])
        self.assertEqual([c["function"] for c in rows[1]["tool_calls"] + rows[3]["tool_calls"]],
                         [{"name": "shell", "arguments": "{\"cmd\": \"date\"}"}, {"name": "exec", "arguments": "date +%s"}])
        self.assertEqual((rows[2]["tool_name"], rows[2]["tool_call_id"]), ("shell", "c1"))
        self.assertEqual({r["ts"] for r in rows}, {int(STAMP.timestamp())})

    def test_the_session_keeps_its_folder_and_branch(self):
        (_sid, _title, meta, _rows), = self.sessions()
        self.assertEqual((meta["cwd"], meta["branch"]), ("/work/demo", "main"))

    def test_the_usage_takes_the_cache_out_of_the_input_and_counts_a_repeated_event_once(self):
        got = list(ports.harnesses()[self.KEY]["usage"](self.path))
        self.assertEqual([(r["req"], r["sess"], r["model"], r["prov"], r["src"]) for r in got],
                         [("abc123:1050", "abc123", "gpt-demo", "openai", "codex"), ("abc123:2280", "abc123", "gpt-demo", "openai", "codex")])
        self.assertEqual([(r["in"], r["cr"], r["cw"], r["out"], r["rsn"], r["prompt"]) for r in got],
                         [(200, 800, 0, 50, 10, 1000), (250, 900, 50, 30, 0, 1200)], "OpenAI's input holds the cache")

    def test_a_broken_line_is_skipped_and_counted(self):
        (_sid, _title, meta, rows), = self.sessions()
        self.assertEqual((meta["skipped"], len(rows)), ("1", 6))


if __name__ == "__main__":
    unittest.main()
