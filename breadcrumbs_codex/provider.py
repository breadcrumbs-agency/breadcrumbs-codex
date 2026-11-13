"""Loopback Responses provider running the original Hermes ACE on outgoing requests."""

import argparse
from collections import OrderedDict
from contextlib import contextmanager
import hmac
import http.server
import json
import os
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

from breadcrumbs import ace
from breadcrumbs.history import Refs
from breadcrumbs.index import Index
from breadcrumbs.store import Store
from breadcrumbs.zoom import Zoom
from . import responses


class Conversation:
    def __init__(self, root, sid, model, context_window):
        self.lock = threading.Lock()
        self.users = 0
        self.engine = ace.Ace()
        self.engine.on_session_start(sid)
        self.engine.root = str(root)
        self.engine.update_model(model, context_window)
        self.engine.store = Store(str(root / "store"))
        try:
            ix = self.engine._index = Index(str(root / "index.db"), readonly=True)
            self.engine.zoom = Zoom(self.engine.store, summaries=ix.summary_of, cues=ix.signature, words=ix.tiers)
        except BaseException:
            self.close()
            raise

    def close(self):
        for resource in (self.engine.store, self.engine._index):
            if resource is not None:
                resource.close()

    def shape(self, body, messages):
        selected = self.engine.select_context(messages)
        return responses.replace_input(body, selected, self.engine.layer) if selected is not None else body

    def observe(self, usage):
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
            self.engine.update_from_response({
                "prompt_tokens": usage["input_tokens"],
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            })


class Conversations:
    def __init__(self, root, context_window):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        Index(str(self.root / "index.db")).close()
        Refs(str(self.root / "refs"))
        self.context_window = context_window
        self.lock = threading.Lock()
        self.entries = OrderedDict()

    @contextmanager
    def use(self, sid, model, window):
        key = sid, model, window
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                entry = self.entries[key] = Conversation(self.root, sid, model, self.context_window)
            entry.users += 1
            self.entries.move_to_end(key)
        try:
            with entry.lock:
                yield entry
        finally:
            with self.lock:
                entry.users -= 1
                for old_key, old in list(self.entries.items()):
                    if len(self.entries) <= 32:
                        break
                    if not old.users:
                        del self.entries[old_key]
                        old.close()

    def close(self):
        with self.lock:
            for entry in self.entries.values():
                entry.close()
            self.entries.clear()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


DROP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding",
                "keep-alive", "proxy-authorization", "proxy-authenticate", "upgrade", "te", "trailer"}
ROUTES = {("GET", "/models"), ("POST", "/responses"), ("POST", "/responses/compact"),
          ("POST", "/responses/input_tokens")}
LOCAL_TOKEN = "X-Breadcrumbs-ACE"
CHATGPT_UPSTREAM = "https://chatgpt.com/backend-api/codex"
API_UPSTREAM = "https://api.openai.com/v1"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def error(self, status, message):
        data = json.dumps({"error": {"message": message, "type": "breadcrumbs_provider"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        auth_header = LOCAL_TOKEN if self.server.managed else "Authorization"
        if not hmac.compare_digest(self.headers.get(auth_header, "").encode(), self.server.authorization):
            return self.error(401, "The ACE provider requires its local credentials.")
        if self.server.managed and self.command == "GET" and self.path == "/health":
            self.send_response(204)
            self.end_headers()
            return
        if self.server.managed and self.command == "POST" and self.path == "/shutdown":
            threading.Thread(target=self.server.shutdown).start()
            self.send_response(204)
            self.end_headers()
            return
        if self.server.managed and not self.headers.get("Authorization"):
            return self.error(401, "Sign in to Codex to use the ACE provider.")
        route = urllib.parse.urlsplit(self.path)
        if route.netloc or not route.path.startswith("/v1/") or (self.command, route.path[3:]) not in ROUTES:
            return self.error(404, "Unknown Responses API route.")
        path = route.path[3:] + (("?" + route.query) if route.query else "")
        raw = None
        if self.command == "POST":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 64 * 1024 * 1024:
                    return self.error(400, "Expected a Responses request with a Content-Length up to 64 MiB.")
                raw = self.rfile.read(size)
            except ValueError:
                return self.error(400, "Invalid Content-Length.")
        if self.command != "POST" or route.path != "/v1/responses":
            return self.forward(path, raw)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            return self.error(400, "Expected a JSON Responses request.")
        if not isinstance(body, dict):
            return self.error(400, "Expected a Responses request object.")
        streaming_requested = body.get("stream") is True
        metadata = body.get("client_metadata") or {}
        try:
            request_kind = json.loads(metadata.get("x-codex-turn-metadata", "{}")).get("request_kind")
            sid = responses.session_id(body, self.headers)
            model = body.get("model")
            items = responses.messages(body)
            can_open = items is not None and responses.has_breadcrumbs(body)
        except (AttributeError, TypeError, ValueError, KeyError):
            return self.forward(path, raw, streaming_requested=streaming_requested)
        if not sid or not model or not items or not can_open or request_kind == "compaction":
            return self.forward(path, raw, streaming_requested=streaming_requested)
        window = metadata.get("x-codex-window-id", "")
        with self.server.conversations.use(str(sid), str(model), str(window)) as conversation:
            try:
                shaped = conversation.shape(body, items)
            except (OSError, TypeError, ValueError, KeyError) as error:
                print("breadcrumbs ACE could not shape this request: %s" % type(error).__name__, file=sys.stderr)
                shaped = body
            if shaped is not body:
                raw = json.dumps(shaped, ensure_ascii=False, separators=(",", ":")).encode()
            self.forward(path, raw, conversation.observe, streaming_requested=streaming_requested)

    def forward(self, path, raw, observe=None, *, streaming_requested=False):
        headers = {name: val for name, val in self.headers.items()
                   if name.lower() not in DROP_HEADERS and name.lower() != LOCAL_TOKEN.lower()}
        upstream = self.server.upstream
        if self.server.managed and self.headers.get("ChatGPT-Account-ID"):
            upstream = CHATGPT_UPSTREAM
        request = urllib.request.Request(upstream + path, data=raw, headers=headers, method=self.command)
        try:
            reply = self.server.opener.open(request, timeout=600)
        except urllib.error.HTTPError as error:
            reply = error
        except (OSError, urllib.error.URLError):
            return self.error(502, "Could not reach the configured Responses provider.")
        with reply:
            # The ChatGPT backend sometimes omits Content-Type on its SSE stream.
            content_type = reply.headers.get("Content-Type", "")
            streaming = ("text/event-stream" in content_type
                         or (reply.status == 200 and not content_type and streaming_requested))
            self.send_response(reply.status)
            for name, val in reply.headers.items():
                if name.lower() not in DROP_HEADERS:
                    self.send_header(name, val)
            self.end_headers()
            if streaming:
                self.stream(reply, observe)
            else:
                data = reply.read()
                if observe and reply.status < 300:
                    try:
                        observe(json.loads(data).get("usage"))
                    except (ValueError, AttributeError):
                        pass
                self.wfile.write(data)

    def stream(self, reply, observe):
        data = []
        while line := reply.readline():
            if observe:
                if line.startswith(b"data:"):
                    data.append(line[5:].strip())
                elif not line.strip() and data:
                    try:
                        event = json.loads(b"\n".join(data))
                        if event.get("type") in ("response.completed", "response.done"):
                            observe(event.get("response", {}).get("usage"))
                    except (ValueError, AttributeError):
                        pass
                    data.clear()
            try:
                self.wfile.write(line)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, port, upstream, key, root, context_window=0, *, local_token=None):
        url = urllib.parse.urlsplit(upstream)
        if (url.query or url.fragment or url.username or not url.hostname or
                (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")))):
            raise ValueError("Set the upstream to an HTTPS Responses API base URL or an HTTP loopback fixture.")
        if not key and not local_token:
            raise ValueError("Set the configured API key environment variable.")
        self.managed = local_token is not None
        self.upstream = upstream.rstrip("/")
        self.authorization = (local_token or "Bearer " + key).encode()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        super().__init__(("127.0.0.1", port), Handler)
        try:
            self.conversations = Conversations(root, context_window)
        except BaseException:
            super().server_close()
            raise

    def server_close(self):
        super().server_close()
        self.conversations.close()


def main():
    codex_home = os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--upstream", default=API_UPSTREAM)
    parser.add_argument("--key-env", default="OPENAI_API_KEY")
    parser.add_argument("--home", default=os.environ.get("BREADCRUMBS_HOME") or os.path.join(codex_home, "breadcrumbs"))
    parser.add_argument("--context-window", type=int, default=0,
                        help="provider model context window for Hermes's 75%% guard; 0 uses the configured ACE target")
    args = parser.parse_args()
    try:
        with Server(args.port, args.upstream, os.environ.get(args.key_env), args.home, args.context_window) as server:
            print("Breadcrumbs ACE provider listening on http://127.0.0.1:%d/v1" % server.server_port, flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        parser.exit(1, "breadcrumbs: %s\n" % error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
