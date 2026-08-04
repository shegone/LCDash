"""Version 1 speech provider public contracts."""

from app.integrations.contracts import (
    SpeechToTextCapability,
    SpeechToTextProvider,
    SynthesizedSpeech,
    TextToSpeechCapability,
    TextToSpeechProvider,
    Transcript,
)

__all__ = [
    "SpeechToTextCapability",
    "SpeechToTextProvider",
    "SynthesizedSpeech",
    "TextToSpeechCapability",
    "TextToSpeechProvider",
    "Transcript",
]
