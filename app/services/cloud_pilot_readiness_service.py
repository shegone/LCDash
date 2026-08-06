"""Presentation-safe, static readiness contract for the disconnected cloud pilot."""

from dataclasses import asdict, dataclass
from typing import Literal


ReadinessState = Literal["ready", "planned", "blocked", "disconnected"]


@dataclass(frozen=True)
class PilotModuleStatus:
    key: str
    label: str
    state: ReadinessState
    summary: str
    advisory_only: bool
    action_available: Literal[False] = False


@dataclass(frozen=True)
class CloudPilotReadinessView:
    contract_version: Literal["1.0"]
    deployment_mode: Literal["synthetic-disconnected"]
    overall_state: Literal["not-activated"]
    source: Literal["static-contract"]
    modules: tuple[PilotModuleStatus, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["modules"] = list(payload["modules"])
        return payload


def get_cloud_pilot_readiness() -> CloudPilotReadinessView:
    """Return a deterministic view model without inspecting any external system."""

    return CloudPilotReadinessView(
        contract_version="1.0",
        deployment_mode="synthetic-disconnected",
        overall_state="not-activated",
        source="static-contract",
        modules=(
            PilotModuleStatus(
                key="dashboard",
                label="Dashboard",
                state="ready",
                summary="Synthetic disconnected dashboard is available for review.",
                advisory_only=False,
            ),
            PilotModuleStatus(
                key="analytics_import",
                label="Analytics import",
                state="blocked",
                summary="Admission review is required before any approved dataset is imported.",
                advisory_only=True,
            ),
            PilotModuleStatus(
                key="document_library",
                label="Document library",
                state="ready",
                summary="Private empty-library foundation is ready; content admission is separate.",
                advisory_only=True,
            ),
            PilotModuleStatus(
                key="rag",
                label="Document answers",
                state="blocked",
                summary="Document approval and protected-data review are required before use.",
                advisory_only=True,
            ),
            PilotModuleStatus(
                key="voice",
                label="Voice",
                state="planned",
                summary="Advisory voice capability is designed but remains dormant.",
                advisory_only=True,
            ),
            PilotModuleStatus(
                key="cad_read_only",
                label="CAD read-only connector",
                state="disconnected",
                summary="Disconnected; a separate documented approval is required before activation.",
                advisory_only=True,
            ),
        ),
    )
