"""The sessions of Codex as a source of the ingest (`python -m breadcrumbs ingest codex [PATH]`).

Codex keeps one JSON-lines file for each session: ~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<session id>.jsonl. Every
record has "type" and "payload". The record types that this reads:

    session_meta    payload.id (the session id), cwd, git.branch, parent_thread_id (a forked or subagent session)
    response_item   payload.type "message" with role "user" or "assistant" (content: parts with a "text"),
                    "function_call" (name, arguments, call_id), "custom_tool_call" (name, input, call_id), and their
                    results "function_call_output" and "custom_tool_call_output" (call_id, output: a text or a list
                    of text parts). The calls after an assistant message join its row.

Every other record type is skipped (reasoning, event_msg, turn_context, world_state, token counts, compacted), and so
are the messages of the role "developer", the agent_message items, and the items that a forked session copied from its
parent (metadata.inherited_user_message: they sit in the file of the parent).

The usage of a model request (usage()): an event_msg of payload.type "token_count" has info.last_token_usage (the last
request) and info.total_token_usage (the session so far), each {input_tokens, cached_input_tokens,
cache_write_input_tokens, output_tokens, reasoning_output_tokens, total_tokens}. Codex writes the same event more than
once: a request is an event whose total_token_usage differs from the one before it, and its id is the session id with
total_tokens of the total (stable when the file is read again). OpenAI counts the cached tokens in input_tokens, so
in = input_tokens - cached_input_tokens - cache_write_input_tokens (at least 0). The model is the one of the last
turn_context, the provider the model_provider of session_meta. A forked session starts its own counts.
"""
import json
import os

from breadcrumbs import usage as usage_record
from breadcrumbs.transcript import JsonLines, Rows, epoch, files, meta, text_parts

ROOT = "~/.codex/sessions"
CALLS = {"function_call": "arguments", "custom_tool_call": "input"}  # the kind of a call -> the key of its arguments
RESULTS = ("function_call_output", "custom_tool_call_output")


def session(path):
    """(id, title, meta, rows) of one session file. The id is the one in its session_meta."""
    lines, rows, head = JsonLines(path), Rows(), {}
    for record in lines:
        item = record.get("payload")
        if not isinstance(item, dict) or (record.get("metadata") or {}).get("inherited_user_message"):
            continue
        ts, kind = epoch(record.get("timestamp")), item.get("type")
        if record.get("type") == "session_meta":
            head = head or item
        elif record.get("type") != "response_item":
            continue
        elif kind == "message" and item.get("role") in ("user", "assistant"):
            rows.say(item["role"], text_parts(item.get("content")), ts)
        elif kind in CALLS:
            rows.call(item.get("call_id"), item.get("name"), item.get(CALLS[kind]), ts)
        elif kind in RESULTS:
            rows.result(item.get("call_id"), text_parts(item.get("output")), ts)
    git = head.get("git") if isinstance(head.get("git"), dict) else {}
    return (head.get("id") or os.path.basename(path)[:-len(".jsonl")], "",
            meta(skipped=lines.bad, cwd=head.get("cwd"), branch=git.get("branch"),
                 parent_session_id=head.get("parent_thread_id")), rows.rows)


def sessions(path=None):
    """The source of the harness codex: the sessions of a file, or of the files under a folder (default:
    ~/.codex/sessions), each (id, title, meta, rows)."""
    for found in files(path, ROOT, "*.jsonl", "*/*/*/*.jsonl"):
        yield session(found)


def requests(path):
    """The usage records of one session file, one for each request (breadcrumbs/usage.py record())."""
    sid, prov, model, total = os.path.basename(path)[:-len(".jsonl")], None, None, None
    for record in JsonLines(path):
        item = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        info = item.get("info") if record.get("type") == "event_msg" and item.get("type") == "token_count" else None
        if record.get("type") == "session_meta" and prov is None:
            sid, prov = item.get("id") or sid, item.get("model_provider") or ""
        elif record.get("type") == "turn_context":
            model = item.get("model") or model
        elif isinstance(info, dict) and isinstance(info.get("last_token_usage"), dict):
            now = json.dumps(info.get("total_token_usage"), sort_keys=True)
            if now == total:
                continue  # the same event again
            total, last = now, info["last_token_usage"]
            got = {k: usage_record.count(last.get(k)) for k in ("input_tokens", "cached_input_tokens",
                                                               "cache_write_input_tokens")}
            yield usage_record.record("codex", sid, "%s:%d" % (sid, usage_record.count((info.get("total_token_usage") or {})
                                                                                      .get("total_tokens"))),
                                      epoch(record.get("timestamp")), model, prov,
                                      got["input_tokens"] - got["cached_input_tokens"] - got["cache_write_input_tokens"],
                                      got["cached_input_tokens"], got["cache_write_input_tokens"], last.get("output_tokens"),
                                      last.get("reasoning_output_tokens"))


def usage(path=None):
    """The usage of the harness codex: the usage records of the files that sessions(path) reads."""
    for found in files(path, ROOT, "*.jsonl", "*/*/*/*.jsonl"):
        yield from requests(found)
