"""The avatar's voice must stay neural-capable and config-independent.

Companion to ``test_cloud_polly_avatar.py``, which pins the wire calls
(``Engine="neural"`` on both, same text and voice across them). This file
pins the layer above: *which voice the avatar resolves in the first place*,
and why that resolution must not be taken from operator configuration.

Neither property is expressed as "must be Ruth". Ruth is MAE's voice today
(Ted, 2026-08-11, replacing Joanna); the invariants that must outlive any
future voice change are:

1. The avatar's voice is **not** derived from ``config.polly_voice``. This
   is the subtle one. ``voice_for_persona(config, "mae")`` returns exactly
   Ruth today, so a well-meaning refactor of ``synthesize_cloud_avatar_speech``
   to use it would pass every existing test -- and silently hand operator
   configuration the power to move the avatar's voice. If a configured voice
   ever lacked neural support, the mouth would stop moving with no code
   change to blame.
2. Whatever voice the avatar resolves is **neural-capable**, because neural
   is the only Polly engine that emits viseme speech marks. Verified against
   live Polly on 2026-08-11: Ruth-neural and Stephen-neural both emit marks;
   the generative engine rejects the request outright with
   ``ValidationException: The selected speech mark type - viseme - is not
   supported for this engine: generative``.
"""

from __future__ import annotations

import unittest

from app.integrations.cloud_ai import (
    CloudAiProviderConfig,
    CloudAiRuntime,
    CloudAiRuntimeUnavailable,
    PollyVoice,
    voice_for_persona,
)
from app.integrations.cloud_ai.contracts import (
    NEURAL_CAPABLE_POLLY_VOICES,
    POLLY_CHAT_ENGINES,
    PollySpeechWithVisemes,
    PollyVisemeMark,
    supports_neural_engine,
)
from app.services.cloud_ai_streaming import synthesize_cloud_avatar_speech


class _RecordingPollyProvider:
    """Captures the request the avatar path actually asked Polly for."""

    def __init__(self) -> None:
        self.requests = []

    def synthesize_with_visemes(self, request) -> PollySpeechWithVisemes:
        self.requests.append(request)
        return PollySpeechWithVisemes(b"synthetic-mp3", (PollyVisemeMark(0, "a"),))

    def synthesize(self, request) -> bytes:
        self.requests.append(request)
        return b"synthetic-mp3"


def _config(polly_voice: PollyVoice) -> CloudAiProviderConfig:
    return CloudAiProviderConfig.from_mapping(
        {
            "mode": "advisory-rag",
            "tenant_id": "logan-synthetic",
            "voice_enabled": True,
            "polly_voice": polly_voice.value,
            "action_tools": [],
        }
    )


def _speak(config: CloudAiProviderConfig, **kwargs) -> _RecordingPollyProvider:
    provider = _RecordingPollyProvider()
    synthesize_cloud_avatar_speech(
        CloudAiRuntime(config, polly=provider),
        config,
        request_id="synthetic-request-4001",
        text="Engine three is on scene.",
        **kwargs,
    )
    return provider


class AvatarVoiceIsNeuralCapableTests(unittest.TestCase):
    def test_resolved_voice_supports_the_neural_engine(self):
        """Stated structurally, so it survives the next voice change."""
        provider = _speak(_config(PollyVoice.RUTH))
        resolved = provider.requests[0].voice
        self.assertTrue(
            supports_neural_engine(resolved),
            f"the avatar resolved {resolved!r}, which cannot render visemes",
        )

    def test_every_voice_that_could_reach_the_avatar_is_neural_capable(self):
        """The avatar rejects non-Ruth voices, so this is a belt-and-braces
        check on the enum itself: if a future voice is added that lacks
        neural support, whoever adds it has to decide deliberately whether
        it may ever reach the avatar rather than discovering it live."""
        for voice in PollyVoice:
            with self.subTest(voice=voice):
                self.assertIn(
                    voice,
                    POLLY_CHAT_ENGINES,
                    "every voice needs a declared chat engine",
                )
        self.assertTrue(
            NEURAL_CAPABLE_POLLY_VOICES,
            "no neural-capable voice left; the avatar cannot speak at all",
        )
        self.assertTrue(
            NEURAL_CAPABLE_POLLY_VOICES.issubset(set(PollyVoice)),
            "the neural-capable set names a voice that is not in the enum",
        )


class AvatarVoiceIgnoresOperatorConfigTests(unittest.TestCase):
    def test_configured_persona_voice_cannot_move_the_avatar(self):
        """The pin must be explicit, not derived from configuration.

        ``voice_for_persona(config, "mae")`` returns ``config.polly_voice``,
        which is Ruth by default -- so a refactor to "just use the persona
        voice" looks harmless and passes every other test in the suite.
        Here the configured voice is deliberately set to Stephen, which is
        JACK's. If the avatar ever follows configuration, this fails.
        """
        config = _config(PollyVoice.STEPHEN)
        # Precondition: the persona helper really would hand back Stephen,
        # so this test is exercising a difference that exists.
        self.assertEqual(voice_for_persona(config, "mae"), PollyVoice.STEPHEN)

        provider = _speak(config)

        resolved = provider.requests[0].voice
        self.assertNotEqual(
            resolved,
            PollyVoice.STEPHEN,
            "the avatar followed operator configuration into JACK's voice",
        )
        self.assertTrue(supports_neural_engine(resolved))

    def test_a_caller_still_cannot_choose_the_avatar_voice(self):
        """Companion to the config case: neither input may move it.

        A valid enum member is used on purpose -- an unknown string would be
        rejected by the enum lookup and prove nothing about the pin.
        """
        with self.assertRaisesRegex(CloudAiRuntimeUnavailable, "polly_voice_not_allowed"):
            _speak(_config(PollyVoice.RUTH), voice=PollyVoice.STEPHEN.value)


if __name__ == "__main__":
    unittest.main()
