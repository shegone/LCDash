"""Version 1 inference provider public contract."""

from app.integrations.contracts import (
    InferenceCapability,
    InferenceProvider,
    InferenceRequest,
    InferenceResponse,
)

__all__ = [
    "InferenceCapability",
    "InferenceProvider",
    "InferenceRequest",
    "InferenceResponse",
]
