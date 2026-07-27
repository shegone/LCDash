import stat
import tempfile
import unittest
from pathlib import Path

from scripts.setup_realtime_secret import (
    CFS_SUBSCRIPTION_LABEL,
    RECORD_LABEL,
    UNIT_SUBSCRIPTION_LABEL,
    ensure_realtime_secret,
    record_subscription_ids,
)


class SetupRealtimeSecretTests(unittest.TestCase):
    def test_creates_secret_and_updates_record_without_rotating_existing_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_path = root / "secrets" / "webhook"
            record_path = root / "credentials.txt"
            record_path.write_text("Existing setting: preserved\n", encoding="utf-8")

            ensure_realtime_secret(secret_path, record_path)
            first_secret = secret_path.read_text(encoding="utf-8").strip()
            ensure_realtime_secret(secret_path, record_path)

            self.assertEqual(len(first_secret), 64)
            self.assertEqual(
                secret_path.read_text(encoding="utf-8").strip(),
                first_secret,
            )
            record = record_path.read_text(encoding="utf-8")
            self.assertIn("Existing setting: preserved", record)
            self.assertEqual(record.count(RECORD_LABEL), 1)
            self.assertIn(f"{RECORD_LABEL}{first_secret}", record)

            if os_name_is_posix():
                self.assertEqual(
                    stat.S_IMODE(secret_path.stat().st_mode),
                    0o600,
                )

    def test_records_subscription_ids_without_duplicate_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = Path(temp_dir) / "credentials.txt"
            record_path.write_text(
                "Existing setting: preserved\n"
                f"{CFS_SUBSCRIPTION_LABEL}old\n"
                f"{UNIT_SUBSCRIPTION_LABEL}old\n",
                encoding="utf-8",
            )

            record_subscription_ids(record_path, 1, 2)
            record_subscription_ids(record_path, 1, 2)

            record = record_path.read_text(encoding="utf-8")
            self.assertIn("Existing setting: preserved", record)
            self.assertEqual(record.count(CFS_SUBSCRIPTION_LABEL), 1)
            self.assertEqual(record.count(UNIT_SUBSCRIPTION_LABEL), 1)
            self.assertIn(f"{CFS_SUBSCRIPTION_LABEL}1", record)
            self.assertIn(f"{UNIT_SUBSCRIPTION_LABEL}2", record)


def os_name_is_posix():
    import os

    return os.name == "posix"


if __name__ == "__main__":
    unittest.main()
