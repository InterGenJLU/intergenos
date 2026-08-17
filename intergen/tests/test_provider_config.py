"""Tests for per-user phone-a-friend provider config persistence."""
import os
import tempfile
import unittest
from pathlib import Path

from intergen import provider_config as pc


class TestProviderConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._path = os.path.join(self._tmp, "config.yml")
        os.environ["INTERGEN_USER_CONFIG"] = self._path

    def tearDown(self):
        os.environ.pop("INTERGEN_USER_CONFIG", None)

    def test_empty_when_no_file(self):
        out = pc.list_providers()
        self.assertEqual(out["providers"], [])
        self.assertIsNone(out["primary"])
        self.assertIn("anthropic", out["available_adapters"])
        self.assertEqual(out["catalog"]["anthropic"]["default_model"], "claude-fable-5")

    def test_upsert_persists_without_key(self):
        entry = pc.upsert_provider("fable", "anthropic", "claude-fable-5")
        self.assertEqual(entry["api_key_keyring_id"], "intergen-provider:fable")
        self.assertNotIn("api_key", entry)
        out = pc.list_providers()
        self.assertEqual(len(out["providers"]), 1)
        self.assertEqual(out["providers"][0]["model"], "claude-fable-5")
        # The written YAML never contains a raw key.
        self.assertNotIn("api_key:", Path(self._path).read_text())

    def test_upsert_replaces_by_name(self):
        pc.upsert_provider("fable", "anthropic", "claude-fable-5")
        pc.upsert_provider("fable", "anthropic", "claude-fable-5-pro")
        out = pc.list_providers()
        self.assertEqual(len(out["providers"]), 1)  # replaced, not duplicated
        self.assertEqual(out["providers"][0]["model"], "claude-fable-5-pro")

    def test_invalid_adapter_rejected(self):
        with self.assertRaises(ValueError):
            pc.upsert_provider("x", "not-a-real-adapter", "m")

    def test_missing_fields_rejected(self):
        with self.assertRaises(ValueError):
            pc.upsert_provider("", "anthropic", "claude-fable-5")
        with self.assertRaises(ValueError):
            pc.upsert_provider("x", "anthropic", "")

    def test_set_primary_requires_existing(self):
        with self.assertRaises(ValueError):
            pc.set_primary("ghost")
        pc.upsert_provider("fable", "anthropic", "claude-fable-5")
        pc.set_primary("fable")
        self.assertEqual(pc.list_providers()["primary"], "fable")

    def test_remove_clears_primary(self):
        pc.upsert_provider("fable", "anthropic", "claude-fable-5")
        pc.set_primary("fable")
        self.assertTrue(pc.remove_provider("fable"))
        out = pc.list_providers()
        self.assertEqual(out["providers"], [])
        self.assertIsNone(out["primary"])  # primary cleared with the provider
        self.assertFalse(pc.remove_provider("fable"))  # already gone

    def test_file_is_user_only_perms(self):
        pc.upsert_provider("fable", "anthropic", "claude-fable-5")
        mode = os.stat(self._path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_keyring_id_is_stable_and_namespaced(self):
        self.assertEqual(pc.keyring_id_for("fable"), "intergen-provider:fable")


if __name__ == "__main__":
    unittest.main()
