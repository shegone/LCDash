import os
import sys
import types
import unittest
from unittest.mock import patch

if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv_stub

from app.config.settings import Settings


PILOT_DATABASE_ENV = {
    "LCDASH_DEPLOYMENT_MODE": "synthetic-disconnected",
    "LCDASH_DATABASE_HOST": "pilot-db.example.internal",
    "LCDASH_DATABASE_PORT": "5432",
    "LCDASH_DATABASE_NAME": "lcdash pilot",
    "LCDASH_DATABASE_USERNAME": "pilot@app",
    "LCDASH_DATABASE_PASSWORD": "synthetic:p@ss/word",
}


class Phase1DatabaseSettingsTests(unittest.TestCase):
    def test_pilot_database_url_is_built_from_separate_values(self):
        with patch.dict(os.environ, PILOT_DATABASE_ENV, clear=True):
            settings = Settings()

        self.assertEqual(
            settings.database_url,
            "postgresql://pilot%40app:synthetic%3Ap%40ss%2Fword@"
            "pilot-db.example.internal:5432/lcdash%20pilot",
        )

    def test_pilot_database_settings_fail_closed_when_a_part_is_missing(self):
        incomplete = dict(PILOT_DATABASE_ENV)
        del incomplete["LCDASH_DATABASE_PASSWORD"]
        with patch.dict(os.environ, incomplete, clear=True):
            with self.assertRaisesRegex(ValueError, "LCDASH_DATABASE_PASSWORD"):
                Settings()

    def test_pilot_rejects_legacy_database_url(self):
        environment = dict(PILOT_DATABASE_ENV)
        environment["DATABASE_URL"] = "postgresql://legacy:secret@example/lcdash"
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL is not accepted"):
                Settings()

    def test_legacy_database_url_remains_available_outside_pilot(self):
        legacy = "postgresql://local:development@127.0.0.1:5432/lcdash"
        with patch.dict(os.environ, {"DATABASE_URL": legacy}, clear=True):
            settings = Settings()

        self.assertEqual(settings.database_url, legacy)

    def test_database_secret_value_is_redacted_from_repr(self):
        with patch.dict(os.environ, PILOT_DATABASE_ENV, clear=True):
            rendered = repr(Settings())

        self.assertNotIn(PILOT_DATABASE_ENV["LCDASH_DATABASE_PASSWORD"], rendered)


if __name__ == "__main__":
    unittest.main()
