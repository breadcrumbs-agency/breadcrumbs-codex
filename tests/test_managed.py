"""Regression tests for optional ACE provider routing."""
import os
import json
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from breadcrumbs_codex import managed


PROFILE = managed.PROFILE_MARKER + """model_provider = \"breadcrumbs_ace\"
model_auto_compact_token_limit = 250000

[model_providers.breadcrumbs_ace]
name = \"Breadcrumbs ACE\"
base_url = \"http://127.0.0.1:8767/v1\"
wire_api = \"responses\"

"""


class ManagedProvider(unittest.TestCase):
    def make(self):
        return managed.Managed()

    def test_desktop_provider_conflict_is_refused_without_mutating_config(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            current = "model_provider = \"another_provider\"\nmodel_auto_compact_token_limit = 100000\n"
            manager = self.make()
            manager.profile.parent.mkdir(parents=True, exist_ok=True)
            manager.profile.write_text(PROFILE)
            manager.desktop.write_text(current)

            with self.assertRaisesRegex(ValueError, "would conflict with ACE"):
                manager.enable_desktop()
            self.assertEqual(manager.desktop.read_text(), current)

    def test_enable_desktop_conflict_is_rejected_before_profile_or_provider_setup(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            manager.desktop.parent.mkdir(parents=True, exist_ok=True)
            manager.desktop.write_text('model_provider = "another_provider"\n')
            with patch.object(manager, "start") as start:
                with self.assertRaisesRegex(ValueError, "would conflict with ACE"):
                    manager.enable(8767, 250000, desktop=True)
            start.assert_not_called()
            self.assertFalse(manager.profile.exists())
            self.assertEqual(manager.desktop.read_text(), 'model_provider = "another_provider"\n')
            self.assertFalse(manager.state.exists())

    def test_desktop_routing_preserves_named_providers_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            current = "[model_providers.another_provider]\nname = \"Another\"\n"
            manager = self.make()
            manager.profile.parent.mkdir(parents=True, exist_ok=True)
            manager.profile.write_text(PROFILE)
            manager.desktop.write_text(current)

            manager.enable_desktop()
            self.assertEqual(manager.desktop.read_text().count(managed.DESKTOP_START), 2)
            self.assertEqual(manager.desktop.read_text().count(managed.DESKTOP_END), 2)
            self.assertEqual(manager.desktop.read_text().count("[model_providers.another_provider]"), 1)
            manager.disable_desktop()
            self.assertEqual(manager.desktop.read_text(), current)

    def test_breadcrumbs_strategy_gives_ace_priority_and_disables_turn_end_compaction(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            with patch.object(manager, "start"):
                manager.enable(8767, strategy="breadcrumbs")

            source = manager.profile.read_text()
            settings = tomllib.loads(source)
            self.assertEqual(manager.strategy(), "breadcrumbs")
            self.assertEqual(settings["model_auto_compact_token_limit"],
                             managed.BREADCRUMBS_COMPACT_LIMIT)
            self.assertEqual(settings["model_post_turn_compact_threshold_percent"], 0)
            self.assertIn("# Breadcrumbs compaction strategy: breadcrumbs", source)

    def test_no_argument_enable_defaults_to_breadcrumbs_and_one_million_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            with patch.object(manager, "start"):
                manager.enable(8767)
            settings = tomllib.loads(manager.profile.read_text())
            self.assertEqual(manager.strategy(), managed.DEFAULT_STRATEGY)
            self.assertEqual(settings["model_auto_compact_token_limit"], managed.BREADCRUMBS_COMPACT_LIMIT)
            self.assertEqual(settings["model_post_turn_compact_threshold_percent"], 0)

    def test_harness_settings_support_pending_values_and_individual_reverts(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            initial = manager.harness_settings()
            self.assertFalse(initial["enabled"])
            self.assertEqual({field["key"]: field["value"] for field in initial["fields"]},
                             {"strategy": managed.DEFAULT_STRATEGY, "compact_limit": managed.BREADCRUMBS_COMPACT_LIMIT})

            manager.harness_settings("set", {"key": "strategy", "value": "hybrid"})
            manager.harness_settings("set", {"key": "compact_limit", "value": 333000})
            self.assertEqual(json.loads(manager.settings_file.read_text()), {"compact_limit": 333000, "strategy": "hybrid"})
            manager.harness_settings("delete", {"key": "compact_limit"})
            values = {field["key"]: field["value"] for field in manager.harness_settings()["fields"]}
            self.assertEqual(values, {"strategy": "hybrid", "compact_limit": managed.HYBRID_COMPACT_LIMIT})
            manager.harness_settings("reset")
            self.assertFalse(manager.settings_file.exists())
            values = {field["key"]: field["value"] for field in manager.harness_settings()["fields"]}
            self.assertEqual(values, {"strategy": managed.DEFAULT_STRATEGY, "compact_limit": managed.BREADCRUMBS_COMPACT_LIMIT})

    def test_setting_strategy_on_an_active_profile_updates_its_fallback_pair(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            with patch.object(manager, "start"):
                manager.enable(8767, strategy="hybrid")
                manager.harness_settings("set", {"key": "strategy", "value": "breadcrumbs"})
            settings = tomllib.loads(manager.profile.read_text())
            self.assertEqual(settings["model_auto_compact_token_limit"], managed.BREADCRUMBS_COMPACT_LIMIT)
            self.assertEqual(manager.strategy(), "breadcrumbs")

    def test_enable_consumes_pending_harness_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            manager.harness_settings("set", {"key": "strategy", "value": "hybrid"})
            manager.harness_settings("set", {"key": "compact_limit", "value": 333000})
            with patch.object(manager, "start"):
                manager.enable(8767)
            settings = tomllib.loads(manager.profile.read_text())
            self.assertEqual(settings["model_auto_compact_token_limit"], 333000)
            self.assertEqual(manager.strategy(), "hybrid")

    def test_reenabling_updates_managed_strategy_and_limit_without_changing_provider(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            with patch.object(manager, "start") as start:
                manager.enable(8767, 250000)
                manager.enable(8767, 400000, strategy="breadcrumbs")

            settings = tomllib.loads(manager.profile.read_text())
            self.assertEqual(settings["model_providers"][managed.PROVIDER]["base_url"],
                             "http://127.0.0.1:8767/v1")
            self.assertEqual(settings["model_auto_compact_token_limit"], 400000)
            self.assertEqual(manager.strategy(), "breadcrumbs")
            self.assertEqual(start.call_count, 2)

    def test_managed_desktop_routing_refreshes_strategy(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            manager = self.make()
            manager.desktop.write_text("[model_providers.another_provider]\nname = \"Another\"\n")
            with patch.object(manager, "start"):
                manager.enable(8767, desktop=True)
                manager.enable(8767, strategy="breadcrumbs", desktop=True)

            settings = tomllib.loads(manager.desktop.read_text())
            self.assertEqual(settings["model_provider"], managed.PROVIDER)
            self.assertEqual(settings["model_auto_compact_token_limit"],
                             managed.BREADCRUMBS_COMPACT_LIMIT)
            self.assertEqual(settings["model_post_turn_compact_threshold_percent"], 0)
            self.assertEqual(manager.desktop.read_text().count(managed.DESKTOP_START), 2)


if __name__ == "__main__":
    unittest.main()
