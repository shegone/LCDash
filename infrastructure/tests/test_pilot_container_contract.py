from pathlib import Path
import re
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPOSITORY_ROOT / "Dockerfile.aws-pilot"


class PilotContainerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DOCKERFILE.read_text(encoding="utf-8")

    def test_explicit_non_root_identity_matches_cdk(self):
        self.assertIn("addgroup -S -g 10001 lcdash", self.source)
        self.assertIn("adduser -S -D -H -u 10001 -G lcdash lcdash", self.source)
        self.assertIn("USER 10001:10001", self.source)

    def test_runtime_port_and_shallow_healthcheck(self):
        self.assertIn("EXPOSE 8000", self.source)
        self.assertIn("http://127.0.0.1:8000/health", self.source)
        self.assertNotIn("http://127.0.0.1:8000/'", self.source)

    def test_runtime_writes_are_bounded_to_tmp(self):
        for setting in (
            "TMPDIR=/tmp",
            "HOME=/tmp/home",
            "XDG_CACHE_HOME=/tmp/cache",
        ):
            self.assertIn(setting, self.source)

    def test_only_narrow_pilot_runtime_paths_are_copied(self):
        copy_sources = []
        for line in self.source.splitlines():
            if line.startswith("COPY "):
                normalized = re.sub(r"^COPY\s+--chown=\S+\s+", "", line)
                copy_sources.append(normalized.split()[1 if normalized.startswith("COPY ") else 0])

        self.assertEqual(
            copy_sources,
            [
                "requirements.txt",
                "app",
                "config/counties/schema.json",
                "config/counties/logan-synthetic.json",
                "database",
                "static",
                "templates",
            ],
        )
        for forbidden in ("docs", "scripts", ".env", "secrets", "credentials"):
            self.assertNotRegex(self.source, rf"(?m)^COPY .*\b{re.escape(forbidden)}\b")


if __name__ == "__main__":
    unittest.main()
