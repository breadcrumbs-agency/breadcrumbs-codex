"""Manage the ACE provider bundled with the Codex plugin."""

import argparse
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import tomllib

from .provider import API_UPSTREAM, LOCAL_TOKEN, Server


ROOT = Path(__file__).resolve().parent.parent
PROFILE = "breadcrumbs-ace"
PROVIDER = "breadcrumbs_ace"
PROFILE_MARKER = "# Managed by the Breadcrumbs Codex plugin.\n"
DESKTOP_START = "# BEGIN Breadcrumbs ACE routing\n"
DESKTOP_END = "# END Breadcrumbs ACE routing\n"


def read(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def write(path, contents, *, replace):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        staging = Path(temporary.name)
        try:
            temporary.write(contents)
            temporary.close()
            if replace:
                staging.replace(path)
            else:
                os.link(staging, path)
        finally:
            staging.unlink(missing_ok=True)


class Managed:
    def __init__(self):
        codex_home = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser().resolve()
        self.profile = codex_home / (PROFILE + ".config.toml")
        self.desktop = codex_home / "config.toml"
        self.store = Path(os.environ.get("BREADCRUMBS_HOME") or codex_home / "breadcrumbs").expanduser().resolve()
        self.state = codex_home / "breadcrumbs-ace"

    @contextmanager
    def lock(self):
        import fcntl

        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.state / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def config(self):
        source = read(self.profile)
        if not source and not self.profile.exists():
            return None
        if not source.startswith(PROFILE_MARKER):
            raise ValueError(f"{self.profile} already exists and is not managed by Breadcrumbs")
        try:
            settings = tomllib.loads(source)["model_providers"][PROVIDER]
            port = int(settings["base_url"].removeprefix("http://127.0.0.1:").removesuffix("/v1"))
            return port, settings["http_headers"][LOCAL_TOKEN]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid Breadcrumbs ACE provider settings in {self.profile}") from error

    def request(self, config, method="GET", path="/health"):
        port, token = config
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
        try:
            connection.request(method, path, headers={LOCAL_TOKEN: token})
            with connection.getresponse() as reply:
                return reply.status == 204
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def start(self, config):
        if self.request(config):
            return
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH"))))
        with (self.state / "provider.log").open("ab") as log:
            child = subprocess.Popen([sys.executable, "-m", "breadcrumbs_codex.managed", "serve"],
                                     env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and child.poll() is None:
            if self.request(config):
                return
            time.sleep(0.05)
        if child.poll() is None:
            child.terminate()
            child.wait()
        raise OSError(f"ACE provider did not start. See {self.state / 'provider.log'}")

    def enable(self, port, compact_limit, desktop=False):
        with self.lock():
            config = self.config()
            if config is None:
                token = secrets.token_urlsafe(32)
                write(self.profile, PROFILE_MARKER + f'''model_provider = "{PROVIDER}"
model_auto_compact_token_limit = {compact_limit}

[model_providers.{PROVIDER}]
name = "Breadcrumbs ACE"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false

[model_providers.{PROVIDER}.http_headers]
{LOCAL_TOKEN} = {json.dumps(token)}
''', replace=False)
                config = port, token
                try:
                    self.start(config)
                except OSError:
                    self.profile.unlink()
                    raise
            else:
                self.start(config)
            if desktop:
                self.enable_desktop()

    def enable_desktop(self):
        source = read(self.desktop)
        if source.count(DESKTOP_START) == source.count(DESKTOP_END) == 2:
            return
        settings = tomllib.loads(source)
        if (DESKTOP_START in source or DESKTOP_END in source or "model_provider" in settings
                or PROVIDER in settings.get("model_providers", {})):
            raise ValueError(f"Existing model provider settings in {self.desktop} would conflict with ACE")
        selection, provider = self.profile.read_text().removeprefix(PROFILE_MARKER).split("\n\n", 1)
        if "model_auto_compact_token_limit" in settings:
            selection = selection.splitlines()[0]
        if source and not source.endswith("\n"):
            source += "\n"
        next_source = (DESKTOP_START + selection + "\n" + DESKTOP_END + source
                       + DESKTOP_START + "\n" + provider + DESKTOP_END)
        tomllib.loads(next_source)
        write(self.desktop, next_source, replace=True)

    def disable_desktop(self):
        source = read(self.desktop)
        if DESKTOP_START not in source and DESKTOP_END not in source:
            return
        if source.count(DESKTOP_START) != 2 or source.count(DESKTOP_END) != 2:
            raise ValueError(f"Incomplete Breadcrumbs ACE routing in {self.desktop}")
        before, start, managed = source.partition(DESKTOP_START)
        _, end, after = managed.partition(DESKTOP_END)
        if not (start and end):
            raise ValueError(f"Incomplete Breadcrumbs ACE routing in {self.desktop}")
        before_provider, _, managed = after.partition(DESKTOP_START)
        _, end, after_provider = managed.partition(DESKTOP_END)
        if not end:
            raise ValueError(f"Incomplete Breadcrumbs ACE routing in {self.desktop}")
        source = before + before_provider + after_provider
        tomllib.loads(source)
        write(self.desktop, source, replace=True)

    def disable(self):
        with self.lock():
            config = self.config()
            self.disable_desktop()
            if config:
                if self.request(config, "POST", "/shutdown"):
                    deadline = time.monotonic() + 5
                    while self.request(config):
                        if time.monotonic() >= deadline:
                            raise OSError("ACE provider is still stopping. Try again in a few seconds.")
                        time.sleep(0.05)
                self.profile.unlink()

    def ensure(self):
        # Most plugin launches use the memory tools alone and do not need a provider.
        if self.profile.exists():
            with self.lock():
                config = self.config()
                if config:
                    self.start(config)

    def serve(self):
        config = self.config()
        if config is None:
            raise ValueError("Enable the Breadcrumbs ACE profile first")
        port, token = config
        with Server(port, API_UPSTREAM, None, self.store, local_token=token) as server:
            print(f"Breadcrumbs ACE listening on 127.0.0.1:{port}", flush=True)
            server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    enable = commands.add_parser("enable", help="create the Codex profile and start ACE")
    enable.add_argument("--port", type=int, default=8767)
    enable.add_argument("--compact-limit", type=int, default=250_000)
    enable.add_argument("--desktop", action="store_true", help="also select ACE for new local desktop chats")
    for command in ("status", "disable", "ensure", "serve"):
        commands.add_parser(command)
    args = parser.parse_args()
    managed = Managed()
    try:
        if args.command == "enable":
            if not 0 < args.port < 65536 or args.compact_limit <= 0:
                parser.error("port and compaction limit must be positive and the port must be below 65536")
            managed.enable(args.port, args.compact_limit, args.desktop)
            if args.desktop:
                print("Breadcrumbs ACE is running and selected for new local Codex chats.")
            else:
                print(f"Breadcrumbs ACE is running. Start Codex with --profile {PROFILE}.")
        elif args.command == "status":
            config = managed.config()
            if not config:
                print("Breadcrumbs ACE is disabled.")
            elif managed.request(config):
                print(f"Breadcrumbs ACE is running on 127.0.0.1:{config[0]}.")
            else:
                print("Breadcrumbs ACE is configured. The plugin will start it when Codex loads the MCP server.")
            if config and tomllib.loads(read(managed.desktop)).get("model_provider") == PROVIDER:
                print("ACE is the default provider for new local Codex chats.")
            print(f"ACE measurements: {managed.store / 'ace.log'}")
        elif args.command == "disable":
            managed.disable()
            print("Breadcrumbs ACE is disabled.")
        elif args.command == "ensure":
            managed.ensure()
        else:
            managed.serve()
    except (OSError, ValueError) as error:
        parser.exit(1, f"breadcrumbs ACE: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
