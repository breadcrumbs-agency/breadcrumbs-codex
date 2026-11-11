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
"""
import os

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
