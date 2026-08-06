from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from app.tools.document_preparation import prepare_documents


ROOT = Path(__file__).resolve().parents[2]


class DocumentPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / "approved").mkdir()
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)

    def request(self, documents):
        path = self.repo / "request.json"
        path.write_text(json.dumps({
            "schema_version": "lcdash.document-preparation.v1",
            "source_root": "approved",
            "documents": documents,
        }), encoding="utf-8")
        return path

    def test_deterministic_inventory_chunks_and_optional_local_embedding(self):
        (self.repo / "approved/manual.md").write_text("Heading\n" + "safe procedure. " * 500, encoding="utf-8")
        item = {"path": "manual.md", "scope": "mindshare-current", "approved": True, "approval_id": "review-1"}
        first = prepare_documents(self.request([item]), repository_root=self.repo, include_local_embeddings=True)
        second = prepare_documents(self.request([item]), repository_root=self.repo, include_local_embeddings=True)
        self.assertEqual(first, second)
        self.assertEqual(first["eligible_count"], 1)
        self.assertGreater(first["eligible"][0]["chunk_count"], 1)
        self.assertEqual(len(first["eligible"][0]["chunks"][0]["local_feature_embedding"]), 64)
        self.assertFalse(first["upload_authorized"])
        self.assertFalse(first["rag_enabled"])
        self.network_mock.assert_not_called()

    def test_unapproved_missing_and_hard_excluded_files_are_not_read(self):
        secret = self.repo / "approved/password-manual.md"
        secret.write_text("must not be extracted", encoding="utf-8")
        documents = [
            {"path": "password-manual.md", "scope": "mindshare-current", "approved": True, "approval_id": "review-1"},
            {"path": "pending.md", "scope": "mindshare-current", "approved": False, "approval_id": ""},
            {"path": "missing.pdf", "scope": "centralsquare-current", "approved": True, "approval_id": "review-2"},
        ]
        result = prepare_documents(self.request(documents), repository_root=self.repo)
        self.assertEqual(result["eligible_count"], 0)
        self.assertEqual({x["reason"] for x in result["rejected"]}, {
            "hard-exclusion", "approval-not-recorded", "source-missing-or-outside-root"
        })
        self.network_mock.assert_not_called()

    def test_scope_specific_type_is_fail_closed(self):
        (self.repo / "approved/catalog.md").write_text("catalog", encoding="utf-8")
        item = {"path": "catalog.md", "scope": "mindshare-software-catalog", "approved": True, "approval_id": "review-3"}
        result = prepare_documents(self.request([item]), repository_root=self.repo)
        self.assertEqual(result["eligible_count"], 0)
        self.assertEqual(result["rejected"][0]["reason"], "type-not-approved-for-scope")

    def test_forbidden_token_does_not_match_inside_to_kenwood(self):
        name = "MS1000_AN_ConsoleToKenwoodAppNote_v100.pdf"
        (self.repo / "approved" / name).write_bytes(b"%PDF synthetic")
        item = {"path": name, "scope": "mindshare-current", "approved": True, "approval_id": "review-kenwood"}
        with patch("app.tools.document_preparation._extract", return_value="approved application note"):
            result = prepare_documents(self.request([item]), repository_root=self.repo)
        self.assertEqual(result["eligible_count"], 1)

    def test_explicit_separate_source_repository_is_supported_without_discovery(self):
        source_temp = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(source_temp.cleanup)
        source = Path(source_temp.name)
        (source / "approved").mkdir()
        (source / "approved/manual.md").write_text("approved content", encoding="utf-8")
        item = {"path": "manual.md", "scope": "mindshare-current", "approved": True, "approval_id": "review-4"}
        result = prepare_documents(
            self.request([item]),
            repository_root=self.repo,
            source_repository_root=source,
        )
        self.assertEqual(result["eligible_count"], 1)
        self.assertEqual(result["eligible"][0]["path"], "manual.md")


if __name__ == "__main__":
    unittest.main()
