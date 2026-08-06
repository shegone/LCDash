import socket
import unittest
from unittest.mock import patch

from app.integrations.cloud_ai import AdvisoryRagRequest
from app.integrations.cloud_ai.bedrock_retrieval import (
    ApprovedBedrockRetriever,
    CitationOnlyBedrockAdvisory,
)


class _Client:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {"retrievalResults": self.results}


class BedrockRetrievalTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("network blocked"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.prefix = "s3://private/tenants/logan-synthetic/document-library/mindshare/current/"

    def retriever(self, client):
        return ApprovedBedrockRetriever(client=client, knowledge_base_id="AB12CD34EF",
            tenant_id="logan-synthetic", allowed_s3_prefixes=(self.prefix,),
            result_limit=5, score_threshold=0.5)

    def test_semantic_retrieve_returns_only_approved_cited_sources(self):
        client = _Client([
            {"score": .91, "content": {"text": "Approved procedure."},
             "location": {"s3Location": {"uri": self.prefix + "manual.pdf"}},
             "metadata": {"title": "Manual", "page_number": 4, "revision": "1.2"}},
            {"score": .99, "content": {"text": "Wrong tenant."},
             "location": {"s3Location": {"uri": "s3://private/other/manual.pdf"}}},
            {"score": .2, "content": {"text": "Low confidence."},
             "location": {"s3Location": {"uri": self.prefix + "low.pdf"}}},
        ])
        results = self.retriever(client).retrieve(tenant_id="logan-synthetic", question="procedure")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].citation.page, 4)
        config = client.calls[0]["retrievalConfiguration"]["vectorSearchConfiguration"]
        self.assertEqual(config["overrideSearchType"], "SEMANTIC")
        self.network.assert_not_called()

    def test_wrong_tenant_and_invalid_query_fail_without_provider_call(self):
        client = _Client([])
        retriever = self.retriever(client)
        self.assertEqual(retriever.retrieve(tenant_id="other", question="test"), ())
        self.assertEqual(retriever.retrieve(tenant_id="logan-synthetic", question=" "), ())
        self.assertEqual(client.calls, [])

    def test_citation_only_provider_returns_excerpts_without_model_call(self):
        client = _Client([
            {"score": .91, "content": {"text": "Approved procedure excerpt."},
             "location": {"s3Location": {"uri": self.prefix + "manual.pdf"}},
             "metadata": {"title": "Manual", "page_number": 4}},
        ])
        provider = CitationOnlyBedrockAdvisory(self.retriever(client))
        response = provider.answer(AdvisoryRagRequest(
            "request-2001", "logan-synthetic", "What is the procedure?"
        ))
        self.assertFalse(response.denied)
        self.assertEqual(response.answer, "Approved procedure excerpt.")
        self.assertEqual(response.citations[0].page, 4)
        self.assertEqual(len(client.calls), 1)

    def test_citation_only_provider_denies_when_no_supported_source(self):
        provider = CitationOnlyBedrockAdvisory(self.retriever(_Client([])))
        response = provider.answer(AdvisoryRagRequest(
            "request-2002", "logan-synthetic", "Unsupported question?"
        ))
        self.assertTrue(response.denied)
        self.assertEqual(response.answer, "")


if __name__ == "__main__":
    unittest.main()
