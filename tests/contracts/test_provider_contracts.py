"""Deterministic contract tests for the Package 1B provider boundary."""

import socket
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.tenancy import CountyProfile, TenantContext
from app.integrations.contracts import (
    CadCapability,
    CapabilityDenied,
    InferenceRequest,
    ModuleCapability,
    PageRequest,
    ProviderRateLimit,
    ProviderTimeout,
    TenantBindingError,
)
from app.integrations.ai.base import InferenceProvider
from app.integrations.ai.synthetic import SyntheticInferenceProvider
from app.integrations.cad.base import CadProvider
from app.integrations.cad.synthetic import SyntheticCadProvider
from app.integrations.knowledge.base import RetrievalProvider
from app.integrations.knowledge.synthetic import SyntheticRetrievalProvider
from app.integrations.speech.base import SpeechToTextProvider, TextToSpeechProvider
from app.integrations.speech.synthetic import (
    SyntheticSpeechToTextProvider,
    SyntheticTextToSpeechProvider,
)


def tenant(tenant_id: str = "synthetic-county", request_id: str = "request-1") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-supervisor",
        identity_source="synthetic-federation",
        roles=frozenset({"supervisor"}),
        request_id=request_id,
        authenticated_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )


def synthetic_calls(count: int = 3):
    return [
        {
            "cfs_number": f"SYN-{index + 1:04d}",
            "incident_code": "SYN",
            "incident_description": "Synthetic incident",
            "priority": "2",
            "agency": "SYN",
            "status": "Open",
            "call_datetime": f"2026-08-04T12:0{index}:00Z",
            "location": "Synthetic location",
            "assigned_units": [f"SYN-{index + 10}"],
            "raw_payload": "must never cross the normalized boundary",
        }
        for index in range(count)
    ]


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.network_blockers = [
            patch.object(socket.socket, "connect", side_effect=AssertionError("network access blocked")),
            patch("socket.create_connection", side_effect=AssertionError("network access blocked")),
            patch("httpx.get", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.post", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.stream", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.Client", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.network_mocks = [blocker.start() for blocker in self.network_blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.network_blockers):
            blocker.stop()

    def assert_no_network(self):
        for network_mock in self.network_mocks:
            network_mock.assert_not_called()

    def test_tenant_context_and_county_profile_are_deeply_immutable(self):
        context = tenant()
        profile = CountyProfile(
            tenant_id=context.tenant_id,
            display_name="Synthetic County",
            timezone="America/New_York",
            region="us-east-1",
            cad_provider="synthetic",
            capabilities=frozenset(capability.value for capability in ModuleCapability),
            modules=frozenset(capability.value for capability in ModuleCapability),
            branding={"colors": {"accent": "cyan"}},
            agencies=({"id": "syn", "name": "Synthetic Agency"},),
            unit_status_mappings={"AVL": "Available"},
            gis_sources=({"id": "synthetic-roads", "fields": ["name", "geometry"]},),
            identity_federation={"provider": "synthetic", "trusted_claim": "tenant"},
            retention={"analytics_days": 30},
            ai_policy={"advisory_only": True, "tools": ["read"]},
            voice_profile={"provider": "synthetic", "optional": True},
            alert_permissions=frozenset(),
        )

        with self.assertRaises(FrozenInstanceError):
            context.tenant_id = "changed"
        with self.assertRaises(TypeError):
            profile.branding["colors"]["accent"] = "red"
        with self.assertRaises(TypeError):
            profile.agencies[0]["name"] = "Changed"
        self.assertEqual(profile.tenant_id, context.tenant_id)
        self.assertEqual(set(profile.capabilities), {item.value for item in ModuleCapability})
        self.assert_no_network()

    def test_all_provider_protocols_are_runtime_satisfied(self):
        context = tenant()
        providers = (
            (SyntheticCadProvider(context), CadProvider),
            (SyntheticInferenceProvider(context), InferenceProvider),
            (SyntheticRetrievalProvider(context, []), RetrievalProvider),
            (SyntheticSpeechToTextProvider(context), SpeechToTextProvider),
            (SyntheticTextToSpeechProvider(context), TextToSpeechProvider),
        )
        for provider, protocol in providers:
            self.assertIsInstance(provider, protocol)
            self.assertEqual(provider.contract_version, "1.0")
        self.assert_no_network()

    def test_cad_normalization_minimizes_output_and_paginates(self):
        context = tenant()
        provider = SyntheticCadProvider(context, calls=synthetic_calls())

        first = provider.search_calls(context, {}, PageRequest(limit=2), timeout_ms=100)
        second = provider.search_calls(context, {}, PageRequest(limit=2, cursor=first.next_cursor), timeout_ms=100)

        self.assertEqual([item.cfs_number for item in first.items], ["SYN-0001", "SYN-0002"])
        self.assertEqual([item.cfs_number for item in second.items], ["SYN-0003"])
        self.assertEqual(first.next_cursor, "2")
        self.assertIsNone(second.next_cursor)
        self.assertFalse(hasattr(first.items[0], "raw_payload"))
        self.assertEqual(first.items[0].tenant_id, context.tenant_id)
        self.assert_no_network()

    def test_cad_write_and_operational_capabilities_deny_by_default(self):
        context = tenant()
        provider = SyntheticCadProvider(context)
        denied_operations = (
            lambda: provider.ingest_event(context, provider.normalize_event(context, "fixture", {"event_id": "1"}), timeout_ms=100),
            lambda: provider.register_subscription(context, "synthetic://callback", timeout_ms=100),
            lambda: provider.update_call(context, "SYN-0001", {}, timeout_ms=100),
            lambda: provider.send_message(context, "SYN-10", "synthetic", timeout_ms=100),
            lambda: provider.acknowledge(context, "SYN-0001", timeout_ms=100),
        )

        for operation in denied_operations:
            with self.assertRaises(CapabilityDenied):
                operation()
        outcomes = [(event.operation, event.outcome, event.detail) for event in provider.audit_events]
        self.assertIn(("register_subscription", "denied", "capability"), outcomes)
        self.assertIn(("update_call", "denied", "capability"), outcomes)
        self.assert_no_network()

    def test_provider_is_bound_to_immutable_tenant_not_client_selection(self):
        provider = SyntheticCadProvider(tenant("synthetic-county"), calls=synthetic_calls(1))
        attacker_context = tenant("other-county")

        with self.assertRaises(TenantBindingError):
            provider.search_calls(attacker_context, {}, PageRequest(), timeout_ms=100)
        event = provider.audit_events[-1]
        self.assertEqual(event.tenant_id, "synthetic-county")
        self.assertEqual(event.outcome, "denied")
        self.assertEqual(event.detail, "tenant_binding")
        self.assert_no_network()

    def test_timeout_is_deterministic_and_sanitized(self):
        context = tenant()
        provider = SyntheticInferenceProvider(context, simulated_latency_ms=25)
        with self.assertRaisesRegex(ProviderTimeout, "deadline exceeded"):
            provider.generate(context, InferenceRequest("synthetic", timeout_ms=10))
        self.assertEqual(provider.audit_events[-1].outcome, "timeout")
        self.assert_no_network()

    def test_rate_limit_returns_retry_metadata_and_audit(self):
        context = tenant()
        provider = SyntheticCadProvider(context, calls=synthetic_calls(1), rate_limit=1)
        provider.search_calls(context, {}, PageRequest(), timeout_ms=100)
        with self.assertRaises(ProviderRateLimit) as caught:
            provider.search_calls(context, {}, PageRequest(), timeout_ms=100)
        self.assertEqual(caught.exception.retry_after_seconds, 1)
        self.assertEqual(provider.audit_events[-1].outcome, "rate_limited")
        self.assert_no_network()

    def test_inference_and_retrieval_redact_sensitive_values(self):
        context = tenant()
        inference = SyntheticInferenceProvider(context)
        retrieval = SyntheticRetrievalProvider(
            context,
            [{"document_id": "doc-1", "title": "Synthetic", "content": "token=fixture-secret approved procedure", "page_number": 1}],
        )

        answer = inference.generate(context, InferenceRequest("password=fixture-value", timeout_ms=100))
        result = retrieval.search(context, "approved", PageRequest(), timeout_ms=100).items[0]

        self.assertIn("password=[REDACTED]", answer.text)
        self.assertNotIn("fixture-value", answer.text)
        self.assertIn("token=[REDACTED]", result.content)
        self.assertNotIn("fixture-secret", result.content)
        self.assertEqual(result.citation, "Synthetic document, page 1")
        self.assert_no_network()

    def test_audit_is_minimized_and_never_contains_request_payload(self):
        context = tenant()
        provider = SyntheticInferenceProvider(context)
        provider.generate(context, InferenceRequest("narrative=private-fixture", timeout_ms=100))

        event = provider.audit_events[-1]
        self.assertEqual(event.tenant_id, context.tenant_id)
        self.assertEqual(event.request_id, context.request_id)
        self.assertEqual(event.outcome, "success")
        self.assertNotIn("private-fixture", repr(event))
        self.assert_no_network()

    def test_speech_contracts_are_deterministic_and_network_free(self):
        context = tenant()
        stt = SyntheticSpeechToTextProvider(context)
        tts = SyntheticTextToSpeechProvider(context)

        transcript = stt.transcribe(context, b"synthetic-audio", language="en-US", timeout_ms=100)
        speech = tts.synthesize(context, "token=fixture-value", voice="synthetic", timeout_ms=100)

        self.assertEqual(transcript.text, "Synthetic transcript")
        self.assertEqual(transcript.language, "en-US")
        self.assertEqual(speech.media_type, "audio/x-synthetic")
        self.assertNotIn(b"fixture-value", speech.audio)
        self.assertIn(b"[REDACTED]", speech.audio)
        self.assert_no_network()

    def test_retrieval_index_capability_is_explicitly_denied(self):
        context = tenant()
        provider = SyntheticRetrievalProvider(context, [])
        with self.assertRaises(CapabilityDenied):
            provider.index(context, [{"document_id": "doc-1"}], timeout_ms=100)
        self.assertEqual(provider.audit_events[-1].detail, "capability")
        self.assert_no_network()

    def test_new_request_id_for_same_bound_tenant_is_allowed(self):
        provider = SyntheticCadProvider(tenant(request_id="request-1"), calls=synthetic_calls(1))
        next_request = tenant(request_id="request-2")
        result = provider.search_calls(next_request, {}, PageRequest(), timeout_ms=100)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(provider.audit_events[-1].request_id, "request-2")
        self.assert_no_network()


if __name__ == "__main__":
    unittest.main()
