import json
from pathlib import Path
import tempfile
import unittest

from infrastructure.tools.generate_container_release_manifest import (
    APPROVED_INPUTS,
    create_source_archive,
    generate_manifest,
)
import zipfile


class ContainerReleaseManifestTests(unittest.TestCase):
    def _fixture(self, root: Path) -> None:
        for relative in APPROVED_INPUTS:
            path = root / relative
            if Path(relative).suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"approved:{relative}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "runtime.txt").write_text(f"approved:{relative}\n", encoding="utf-8")

    def test_output_is_deterministic_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            first = generate_manifest(root)
            second = generate_manifest(root)
        self.assertEqual(first, second)
        paths = [entry["path"] for entry in first["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(first["source_manifest_sha256"]), 64)
        self.assertIsNone(first["ecr_image_digest"])

    def test_content_change_changes_file_and_aggregate_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            before = generate_manifest(root)
            (root / "app" / "runtime.txt").write_text("changed\n", encoding="utf-8")
            after = generate_manifest(root)
        self.assertNotEqual(before["source_manifest_sha256"], after["source_manifest_sha256"])
        before_entry = next(e for e in before["files"] if e["path"] == "app/runtime.txt")
        after_entry = next(e for e in after["files"] if e["path"] == "app/runtime.txt")
        self.assertNotEqual(before_entry["sha256"], after_entry["sha256"])

    def test_forbidden_and_unapproved_paths_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            forbidden = (
                "app/.env",
                "app/tests/test_hidden.py",
                "app/backups/copy.sql",
                "app/credentials.json",
                "docs/private.md",
                "scripts/deploy.ps1",
                "tests/test_root.py",
            )
            for relative in forbidden:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("must-not-appear\n", encoding="utf-8")
            manifest = generate_manifest(root)
        serialized = json.dumps(manifest)
        for relative in forbidden:
            self.assertNotIn(relative, serialized)
        self.assertNotIn("must-not-appear", serialized)

    def test_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            external = root / "external-secret.txt"
            external.write_text("protected-external-value", encoding="utf-8")
            link = root / "app" / "external-link.txt"
            try:
                link.symlink_to(external)
            except OSError:
                self.skipTest("symlinks are unavailable on this platform")
            manifest = generate_manifest(root)
        serialized = json.dumps(manifest)
        self.assertNotIn("external-link", serialized)
        self.assertNotIn("protected-external-value", serialized)

    def test_missing_required_input_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            (root / "requirements.txt").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "required container input"):
                generate_manifest(root)

    def test_source_archive_contains_exactly_manifested_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            root.mkdir()
            self._fixture(root)
            (root / ".env").write_text("forbidden", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "hidden.py").write_text("forbidden", encoding="utf-8")
            archive_path = Path(temporary) / "source.zip"
            manifest = create_source_archive(archive_path, root)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
        self.assertEqual(names, [entry["path"] for entry in manifest["files"]])
        self.assertNotIn(".env", names)
        self.assertNotIn("tests/hidden.py", names)



if __name__ == "__main__":
    unittest.main()
