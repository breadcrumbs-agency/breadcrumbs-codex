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
STRATEGY_MARKER = "# Breadcrumbs compaction strategy: "
STRATEGIES = ("hybrid", "breadcrumbs")
DEFAULT_STRATEGY = "breadcrumbs"
HYBRID_COMPACT_LIMIT = 250_000
# Codex does not document a value that disables model_auto_compact_token_limit.
# This high-water mark gives ACE the first opportunity while retaining a native
# fallback for unusually long-context models.
BREADCRUMBS_COMPACT_LIMIT = 1_000_000
LEGACY_STRATEGY = "hybrid"
DEFAULT_THRESHOLD_TOKENS = 150_000
DEFAULT_FOLD_TOKENS = 5_000
HARNESS_SETTINGS = (
    {"key": "native_compaction", "label": "Codex native compaction", "kind": "bool", "default": False,
     "doc": "On: Codex compacts at its own threshold (the compact limit below, 250,000 by default) and ACE only adds a layer. "
            "Off: ACE shapes the requests first, Codex's turn-end compaction is off, and its auto-compactor stays as a "
            "high-water fallback (Codex has no value that turns it off). Applies to a chat started after the change."},
    {"key": "compact_limit", "label": "Native fallback compact limit", "kind": "int",
     "default": BREADCRUMBS_COMPACT_LIMIT, "min": 1,
     "doc": "Codex's numeric auto-compaction threshold, in tokens: the fallback when native compaction is off. "
            "Applies to a chat started after the change."},
    {"key": "threshold_tokens", "label": "Hook threshold", "kind": "int", "default": DEFAULT_THRESHOLD_TOKENS, "min": 1,
     "doc": "The size in tokens at which the hooks mark a usage line over_threshold. A diagnostic: it does not make Codex compact. "
            "The environment variable BREADCRUMBS_CODEX_THRESHOLD_TOKENS wins."},
    {"key": "fold_tokens", "label": "PreCompact layer size", "kind": "int", "default": DEFAULT_FOLD_TOKENS, "min": 1000,
     "doc": "The size in tokens of the layer that PreCompact prepares before Codex's own compaction. "
            "The environment variable BREADCRUMBS_CODEX_FOLD_TOKENS wins."},
)
# The settings that are not in the profile: the hooks read them from settings.json beside the lock.
HOOK_SETTINGS = ("threshold_tokens", "fold_tokens")
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
        self.settings_file = self.state / "settings.json"

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

    def strategy(self):
        """Return the managed profile's compaction strategy.

        Profiles created before strategies were introduced are the original
        hybrid behavior, so the missing marker is intentionally backward
        compatible.
        """
        source = read(self.profile)
        if not source:
            return None
        if not source.startswith(PROFILE_MARKER):
            raise ValueError(f"{self.profile} already exists and is not managed by Breadcrumbs")
        line = next((line for line in source.splitlines() if line.startswith(STRATEGY_MARKER)), "")
        # A profile made before the strategy marker existed was the original
        # hybrid behavior.  Do not silently reinterpret that installed profile.
        value = line.removeprefix(STRATEGY_MARKER).strip() if line else LEGACY_STRATEGY
        if value not in STRATEGIES:
            raise ValueError(f"Invalid Breadcrumbs compaction strategy in {self.profile}")
        return value

    @staticmethod
    def compact_limit(strategy, compact_limit):
        if strategy not in STRATEGIES:
            raise ValueError("compaction strategy must be hybrid or breadcrumbs")
        if compact_limit is None:
            return (BREADCRUMBS_COMPACT_LIMIT if strategy == "breadcrumbs" else HYBRID_COMPACT_LIMIT)
        if compact_limit <= 0:
            raise ValueError("native compaction limit must be positive")
        return compact_limit

    def _profile_values(self):
        config = self.config()
        if config is None:
            return None
        source = read(self.profile)
        try:
            parsed = tomllib.loads(source)
            limit = int(parsed["model_auto_compact_token_limit"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid compaction settings in {self.profile}") from error
        return {"strategy": self.strategy() or LEGACY_STRATEGY, "compact_limit": limit,
                "port": config[0], "token": config[1]}

    def _preferences(self):
        try:
            with self.settings_file.open(encoding="utf-8") as f:
                value = json.load(f)
        except (FileNotFoundError, OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_preferences(self, preferences):
        if preferences:
            self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
            write(self.settings_file, json.dumps(preferences, sort_keys=True) + "\n", replace=True)
        else:
            self.settings_file.unlink(missing_ok=True)

    def _harness_settings_view(self):
        """Describe the Codex-specific settings for the common explorer widget."""
        profile = self._profile_values()
        preferences = self._preferences()
        strategy = (profile or {}).get("strategy", preferences.get("strategy", DEFAULT_STRATEGY))
        values = {
            "native_compaction": strategy == "hybrid",
            "compact_limit": (profile or {}).get("compact_limit", preferences.get("compact_limit",
                                                                            self.compact_limit(strategy, None))),
            "threshold_tokens": preferences.get("threshold_tokens", DEFAULT_THRESHOLD_TOKENS),
            "fold_tokens": preferences.get("fold_tokens", DEFAULT_FOLD_TOKENS),
        }
        stored = {"native_compaction": "strategy"}
        result = []
        for field in HARNESS_SETTINGS:
            key = field["key"]
            item = dict(field)
            item["value"] = values[key]
            saved = stored.get(key, key) in preferences
            item["source"] = ("saved" if saved else
                               "profile" if profile and key in ("native_compaction", "compact_limit")
                                            and values[key] != field["default"] else
                               "strategy default" if key == "compact_limit" and values[key] != field["default"] else "default")
            item["overridden"] = saved or (profile is not None and key in ("native_compaction", "compact_limit")
                                           and values[key] != field["default"])
            result.append(item)
        return {"key": "codex", "label": "Codex", "description": "Codex-specific compaction routing, fallback and hook settings.",
                "profile": str(self.profile), "enabled": profile is not None, "fields": result}

    @staticmethod
    def _stored_key(key):
        """The key of settings.json for a field of the widget (the widget's compaction switch is the stored strategy)."""
        return "strategy" if key == "native_compaction" else key

    def harness_settings(self, action="get", payload=None):
        """CRUD for Codex settings, exposed through the common settings widget."""
        payload = payload if isinstance(payload, dict) else {}
        with self.lock():
            active_profile = self._profile_values()
            preferences = self._preferences()
            reset_key = None
            if action == "get":
                return self._harness_settings_view()
            if action not in ("set", "delete", "reset"):
                raise ValueError("unknown Codex settings action: %s" % action)
            known = {field["key"] for field in HARNESS_SETTINGS} | {"strategy"}
            if action == "set":
                key = payload.get("key")
                field = next((f for f in HARNESS_SETTINGS if f["key"] == key), None)
                if field is None and key != "strategy":
                    raise ValueError("unknown Codex setting: %s" % key)
                value = payload.get("value")
                if key == "native_compaction":
                    if not isinstance(value, bool):
                        raise ValueError("native_compaction must be true or false")
                    key, value = "strategy", "hybrid" if value else "breadcrumbs"
                    payload = dict(payload, key=key)
                elif key == "strategy":
                    if value not in STRATEGIES:
                        raise ValueError("strategy must be breadcrumbs or hybrid")
                elif key in ("compact_limit", "threshold_tokens", "fold_tokens"):
                    floor = field.get("min", 1)
                    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
                        raise ValueError("%s must be a whole number of %d or more" % (key, floor))
                preferences[key] = value
            elif action == "reset":
                key = payload.get("key")
                if key and key not in known:
                    raise ValueError("unknown Codex setting: %s" % key)
                key = self._stored_key(key) if key else key
                reset_key = key or "*"
                if key:
                    preferences.pop(key, None)
                else:
                    preferences.clear()
            else:  # delete is the per-field revert operation
                reset_key = self._stored_key(payload.get("key"))
                if reset_key not in known:
                    raise ValueError("unknown Codex setting: %s" % payload.get("key"))
                preferences.pop(reset_key, None)
            self._save_preferences(preferences)

            # The hook settings are read from settings.json by the hooks: the profile and the provider stay as they are.
            changed = payload.get("key") if action == "set" else reset_key
            if active_profile is not None and (changed in (None, "*") or changed not in HOOK_SETTINGS):
                strategy = preferences.get("strategy", active_profile["strategy"])
                limit = preferences.get("compact_limit", active_profile["compact_limit"])
                # A strategy and its native fallback are one pair.  If the
                # strategy changes without an explicit limit override, use
                # that strategy's default instead of carrying the old pair
                # across (hybrid=250k, Breadcrumbs-first=1M).
                if action == "set" and payload.get("key") == "strategy" and "compact_limit" not in preferences:
                    limit = self.compact_limit(strategy, None)
                if reset_key in ("strategy", "*"):
                    strategy = DEFAULT_STRATEGY
                    if "compact_limit" not in preferences:
                        limit = self.compact_limit(strategy, None)
                if reset_key in ("compact_limit", "*"):
                    limit = self.compact_limit(strategy, None)
                if action in ("delete", "reset") and not any(k in preferences for k in ("strategy", "compact_limit")):
                    strategy, limit = DEFAULT_STRATEGY, self.compact_limit(DEFAULT_STRATEGY, None)
                write(self.profile, self.profile_text(active_profile["port"], active_profile["token"], limit, strategy), replace=True)
                if self.desktop.exists() and self._desktop_state()[1]:
                    self.enable_desktop()
                self.start((active_profile["port"], active_profile["token"]))
            return self._harness_settings_view()

    @staticmethod
    def profile_text(port, token, compact_limit, strategy):
        post_turn = ("model_post_turn_compact_threshold_percent = 0\n"
                     if strategy == "breadcrumbs" else "")
        return PROFILE_MARKER + f'''model_provider = "{PROVIDER}"
{STRATEGY_MARKER}{strategy}
model_auto_compact_token_limit = {compact_limit}
{post_turn}
[model_providers.{PROVIDER}]
name = "Breadcrumbs ACE"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false

[model_providers.{PROVIDER}.http_headers]
{LOCAL_TOKEN} = {json.dumps(token)}
'''

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

    def enable(self, port, compact_limit=None, desktop=False, strategy=None):
        if desktop:
            self._desktop_state()
        with self.lock():
            # Validate the desktop target before creating the profile or starting a
            # provider.  A conflicting default must be a no-op, not a half-installed
            # ACE profile that only fails when routing is applied at the end.
            if desktop:
                self._desktop_state()
            config = self.config()
            profile_settings = self._profile_values() if config else None
            preferences = self._preferences()
            strategy = strategy or (profile_settings or {}).get("strategy") or preferences.get("strategy") or DEFAULT_STRATEGY
            compact_limit = (compact_limit if compact_limit is not None else
                             (profile_settings or {}).get("compact_limit") or preferences.get("compact_limit"))
            compact_limit = self.compact_limit(strategy, compact_limit)
            if config is None:
                token = secrets.token_urlsafe(32)
                write(self.profile, self.profile_text(port, token, compact_limit, strategy), replace=False)
                config = port, token
                try:
                    self.start(config)
                except OSError:
                    self.profile.unlink()
                    raise
            else:
                self.start(config)
                # Keep re-enabling an existing managed profile useful: it is
                # also the settings update path for the strategy and limit.
                write(self.profile, self.profile_text(config[0], config[1], compact_limit, strategy),
                      replace=True)
            if desktop:
                self.enable_desktop()

    def _desktop_state(self):
        """Return (source, already_managed) or fail without changing the desktop config."""
        source = read(self.desktop)
        managed = source.count(DESKTOP_START) == source.count(DESKTOP_END) == 2
        if managed:
            # Keep the idempotent path safe even if an external edit left invalid
            # TOML behind the managed markers.
            tomllib.loads(source)
            return source, True
        settings = tomllib.loads(source)
        if (DESKTOP_START in source or DESKTOP_END in source or "model_provider" in settings
                or PROVIDER in settings.get("model_providers", {})):
            raise ValueError(f"Existing model provider settings in {self.desktop} would conflict with ACE")
        return source, False

    def enable_desktop(self):
        source, managed = self._desktop_state()
        settings = tomllib.loads(source)
        selection, provider = self.profile.read_text().removeprefix(PROFILE_MARKER).split("\n\n", 1)
        if "model_auto_compact_token_limit" in settings:
            # A pre-existing desktop threshold belongs to the user.  Keep it
            # outside the managed routing block while still applying the
            # Breadcrumbs strategy and any post-turn setting.
            current_selection = ""
            if managed:
                _, current_selection, _, _, _ = self._desktop_parts(source)
            if not managed or "model_auto_compact_token_limit" not in current_selection:
                selection = "\n".join(line for line in selection.splitlines()
                                       if not line.startswith("model_auto_compact_token_limit"))
        if managed:
            next_source = self._replace_desktop_parts(source, selection, provider)
            tomllib.loads(next_source)
            write(self.desktop, next_source, replace=True)
            return
        if source and not source.endswith("\n"):
            source += "\n"
        next_source = (DESKTOP_START + selection + "\n" + DESKTOP_END + source
                       + DESKTOP_START + "\n" + provider + DESKTOP_END)
        tomllib.loads(next_source)
        write(self.desktop, next_source, replace=True)

    @staticmethod
    def _desktop_parts(source):
        """Return the text around the two managed desktop blocks."""
        before, first_start, rest = source.partition(DESKTOP_START)
        selection, first_end, rest = rest.partition(DESKTOP_END)
        middle, second_start, rest = rest.partition(DESKTOP_START)
        provider, second_end, after = rest.partition(DESKTOP_END)
        if not (first_start and first_end and second_start and second_end):
            raise ValueError("Incomplete Breadcrumbs ACE routing")
        return before, selection, middle, provider, after

    @classmethod
    def _replace_desktop_parts(cls, source, selection, provider):
        before, _, middle, _, after = cls._desktop_parts(source)
        return (before + DESKTOP_START + selection + "\n" + DESKTOP_END + middle
                + DESKTOP_START + "\n" + provider + DESKTOP_END + after)

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
    enable.add_argument("--strategy", choices=STRATEGIES, default=None,
                        help="compaction owner: breadcrumbs gives ACE priority (default), hybrid keeps Codex native compaction")
    enable.add_argument("--compact-limit", type=int, default=None,
                        help="native Codex fallback threshold; defaults to 1000000 (breadcrumbs) or 250000 (hybrid)")
    enable.add_argument("--desktop", action="store_true", help="also select ACE for new local desktop chats")
    for command in ("status", "disable", "ensure", "serve"):
        commands.add_parser(command)
    args = parser.parse_args()
    managed = Managed()
    try:
        if args.command == "enable":
            if not 0 < args.port < 65536:
                parser.error("port must be positive and below 65536")
            if args.compact_limit is not None and args.compact_limit <= 0:
                parser.error("native compaction limit must be positive")
            managed.enable(args.port, args.compact_limit, args.desktop, args.strategy)
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
            print(f"Compaction strategy: {managed.strategy()}.")
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
