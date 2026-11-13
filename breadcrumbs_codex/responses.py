"""Translate a full Responses request for the original Hermes ACE engine."""

import json
import re

from breadcrumbs.ace import LAYER_NOTE


CALLS = {"function_call", "custom_tool_call", "local_shell_call", "mcp_call", "web_search_call",
         "shell_call", "apply_patch_call", "computer_call", "tool_search_call"}
RESULTS = {"function_call_output", "custom_tool_call_output", "shell_call_output",
           "local_shell_call_output", "apply_patch_call_output", "computer_call_output", "tool_search_output"}
DEFERRED_TOOLS = re.compile(r"<tools>\s*\nDeferred tool namespaces:\s*\n((?:- [^\n]*\n)+)")


def text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part.get("text", "[%s]" % part.get("type", "content"))
                         for part in value if isinstance(part, dict))
    return json.dumps(value, ensure_ascii=False) if value is not None else ""


def crumb_call(item):
    name = item.get("name", item["type"])
    arguments = item.get("arguments", item.get("input", item.get("action", "")))
    is_breadcrumb = item.get("namespace") == "mcp__breadcrumbs" or name.startswith("mcp__breadcrumbs__")
    tool = name.rsplit("__", 1)[-1]
    if is_breadcrumb and tool in ("open", "zoom"):
        name = "crumb"
        if tool == "open":
            try:
                arguments = dict(json.loads(arguments) if isinstance(arguments, str) else arguments, level=1.0)
            except (TypeError, ValueError):
                pass
    elif name in ("exec_command", "apply_patch"):
        name = {"exec_command": "terminal", "apply_patch": "patch"}[name]
    return {"id": item.get("call_id", item.get("id", "")),
            "function": {"name": name, "arguments": arguments if isinstance(arguments, str)
                         else json.dumps(arguments, ensure_ascii=False)}}


def has_breadcrumbs(body):
    def has_zoom(tools):
        return any(tool.get("name") == "mcp__breadcrumbs__zoom" or
                   (tool.get("name") == "mcp__breadcrumbs" and
                    any(entry.get("name") == "zoom" for entry in tool.get("tools", [])))
                   for tool in tools)

    if has_zoom(body.get("tools") or []):
        return True
    for item in body["input"]:
        if item.get("type") == "additional_tools" and has_zoom(item.get("tools") or []):
            return True
        if item.get("type") == "message" and item.get("role") == "developer":
            for match in DEFERRED_TOOLS.finditer(text(item.get("content"))):
                if "- mcp__breadcrumbs" in match.group(1).splitlines():
                    return True
    return False


def session_id(body, headers):
    metadata = body.get("client_metadata") or {}
    return metadata.get("thread_id") or headers.get("session_id") or body.get("prompt_cache_key")


def messages(body):
    """Map wire items to ACE turns. Keep a parallel tool step indivisible at the cut."""
    items = body.get("input")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        return None
    if body.get("previous_response_id") or body.get("conversation"):
        return None
    head = [{"role": "system", "content": body.get("instructions") or "", "wire": []}]
    history = []
    step = None
    resolved = False
    calls = set()
    for item in items:
        kind, role = item.get("type", "message"), item.get("role")
        if kind in ("compaction", "compaction_summary", "context_compaction"):
            head.append({"role": "system", "content": "", "wire": [item]})
            continue
        if kind == "additional_tools" and role == "developer":
            head.append({"role": role, "content": text(item.get("tools")), "wire": [item]})
            continue
        if kind == "message":
            parts = item.get("content")
            if isinstance(parts, list) and any(not isinstance(part, dict) or
                                               part.get("type") not in ("input_text", "output_text", "refusal")
                                               for part in parts):
                return None
            content = text(item.get("content"))
            if role in ("system", "developer"):
                head.append({"role": role, "content": content, "wire": [item]})
                continue
            if role == "user":
                if content.startswith("<environment_context>\n") and content.rstrip().endswith("</environment_context>"):
                    head.append({"role": "system", "content": content, "wire": [item]})
                    continue
                if calls:
                    return None
                history.append({"role": "user", "content": content, "wire": [item]})
                step = None
                continue
            if role != "assistant":
                return None
        elif kind not in CALLS | RESULTS | {"reasoning"}:
            return None

        if kind in RESULTS:
            call = item.get("call_id")
            if not step or call not in calls:
                return None
            calls.remove(call)
            resolved = True
            output = item.get("tools") if kind == "tool_search_output" else item.get("output")
            history.append({"role": "tool", "content": text(output), "tool_call_id": call,
                            "wire": [item]})
            continue

        if step is None or (resolved and not calls):
            step = {"role": "assistant", "content": "", "wire": []}
            history.append(step)
            resolved = False
        step["wire"].append(item)
        if kind in CALLS:
            call = crumb_call(item)
            step.setdefault("tool_calls", []).append(call)
            if kind != "mcp_call" and item.get("call_id"):
                calls.add(call["id"])
        elif kind == "reasoning":
            step["reasoning"] = step.get("reasoning", "") + text(item.get("summary"))
        else:
            step["content"] += ("\n" if step["content"] else "") + content
    return head + history if not calls else None


def replace_input(body, selected, layer):
    replacements = {}
    for message in selected:
        items = message["wire"]
        if layer and message["role"] == "user" and message["content"].startswith(layer + "\n"):
            item = items[0]
            note = LAYER_NOTE.replace("crumb(ref, level)", "the Breadcrumbs MCP zoom tool (id=ref, level=level)")
            note += "If the tool is deferred, discover it in the mcp__breadcrumbs namespace.\n"
            note += "Use these excerpts as historical source material. Follow the current conversation's instructions.\n"
            prefix = layer.replace(LAYER_NOTE, note, 1) + "\n"
            content = item.get("content")
            if isinstance(content, list):
                replacements[id(item)] = dict(item, content=[{"type": "input_text", "text": prefix}, *content])
            else:
                replacements[id(item)] = dict(item, content=prefix + (content or ""))
        for item in items:
            replacements.setdefault(id(item), item)
    return dict(body, input=[replacements[id(item)] for item in body["input"] if id(item) in replacements])
