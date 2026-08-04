"""Offline loader and security validation for version 1 county profiles."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from app.core.tenancy import CountyProfile, TENANCY_CONTRACT_VERSION
from app.integrations.contracts import ModuleCapability


COUNTY_PROFILE_VERSION = "1.0"
COUNTY_PROFILE_DIRECTORY = Path(__file__).parents[2] / "config" / "counties"
COUNTY_PROFILE_SCHEMA = COUNTY_PROFILE_DIRECTORY / "schema.json"

_SECRET_KEY = re.compile(
    r"(?i)(password|passcode|secret|token|api[-_ ]?key|credential|private[-_ ]?key)"
)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_TIMEZONE = re.compile(r"^[A-Za-z_]+(?:/[A-Za-z0-9_+.-]+)+$")
_REGION = re.compile(r"^(?:us|us-gov)-[a-z]+-[0-9]+$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGO_ASSET = re.compile(r"^synthetic/[a-z0-9/_-]+\.(?:svg|png)$")
_ABBREVIATION = re.compile(r"^[A-Z0-9-]{2,16}$")
_CLAIM = re.compile(r"^[a-zA-Z][a-zA-Z0-9_:.-]{1,80}$")
_LAYER = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
_UNIT_STATUS_KEY = re.compile(r"^[A-Za-z0-9 _-]{1,80}$")

_REQUIRED_FIELDS = frozenset(
    {
        "profile_version",
        "contract_version",
        "tenant_id",
        "display_name",
        "timezone",
        "region",
        "cad_provider",
        "capabilities",
        "modules",
        "branding",
        "agencies",
        "unit_status_mappings",
        "gis_sources",
        "identity_federation",
        "retention",
        "ai_policy",
        "voice_profile",
        "alert_permissions",
    }
)
_MODULE_CAPABILITIES = frozenset(item.value for item in ModuleCapability)
_ALERT_PERMISSIONS = frozenset(
    {
        "station_alert_preview",
        "ems_delay_dry_run",
        "paging_preview",
        "public_warning_preview",
    }
)
_DISCIPLINES = frozenset(
    {"dispatch", "law", "fire", "ems", "emergency-management"}
)
_GIS_SOURCE_TYPES = frozenset(
    {"synthetic_geojson", "authoritative_reference", "managed_map"}
)
_GIS_DATA_CLASSES = frozenset({"public_synthetic", "county_authoritative"})
_AI_TOOLS = frozenset(
    {"read_calls", "read_units", "read_analytics", "read_knowledge", "read_gis"}
)


class CountyProfileValidationError(ValueError):
    """Raised when a county profile violates the non-secret version 1 contract."""


def _reject_secret_keys(value: Any, path: str = "profile") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY.search(key_text):
                raise CountyProfileValidationError(
                    f"Secret or credential-shaped key is forbidden at {path}.{key_text}."
                )
            _reject_secret_keys(item, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")


def _exact_keys(value: Any, expected: set[str] | frozenset[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CountyProfileValidationError(f"{path} must be an object.")
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise CountyProfileValidationError(
            f"{path} is missing required fields: {', '.join(sorted(missing))}."
        )
    if unknown:
        raise CountyProfileValidationError(
            f"{path} contains unsupported fields: {', '.join(sorted(unknown))}."
        )
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise CountyProfileValidationError(f"{path} must be a non-empty string.")
    return value


def _unique_strings(value: Any, path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CountyProfileValidationError(f"{path} must be an array of strings.")
    if not allow_empty and not value:
        raise CountyProfileValidationError(f"{path} cannot be empty.")
    if len(set(value)) != len(value):
        raise CountyProfileValidationError(f"{path} cannot contain duplicates.")
    return tuple(value)


def validate_county_profile_data(data: Any) -> Mapping[str, Any]:
    """Validate the security-critical schema invariants without network or extras."""

    _reject_secret_keys(data)
    profile = _exact_keys(data, _REQUIRED_FIELDS, "profile")

    if profile["profile_version"] != COUNTY_PROFILE_VERSION:
        raise CountyProfileValidationError("Unsupported county profile version.")
    if profile["contract_version"] != TENANCY_CONTRACT_VERSION:
        raise CountyProfileValidationError("County profile contract version mismatch.")

    tenant_id = _string(profile["tenant_id"], "profile.tenant_id")
    cad_provider = _string(profile["cad_provider"], "profile.cad_provider")
    if not _IDENTIFIER.fullmatch(tenant_id) or not _IDENTIFIER.fullmatch(cad_provider):
        raise CountyProfileValidationError("Tenant and CAD provider IDs must be stable identifiers.")
    display_name = _string(profile["display_name"], "profile.display_name")
    if len(display_name) > 120:
        raise CountyProfileValidationError("County profile display name is too long.")
    if not _TIMEZONE.fullmatch(_string(profile["timezone"], "profile.timezone")):
        raise CountyProfileValidationError("County profile timezone must be an IANA-style name.")
    if not _REGION.fullmatch(_string(profile["region"], "profile.region")):
        raise CountyProfileValidationError("County profile region is invalid.")

    capabilities = frozenset(
        _unique_strings(profile["capabilities"], "profile.capabilities", allow_empty=False)
    )
    modules = frozenset(_unique_strings(profile["modules"], "profile.modules"))
    if not capabilities <= _MODULE_CAPABILITIES:
        raise CountyProfileValidationError("County profile declares an unknown capability.")
    if not modules <= capabilities:
        raise CountyProfileValidationError("Enabled modules must be declared capabilities.")

    branding = _exact_keys(
        profile["branding"],
        {"short_name", "logo_asset", "primary_color", "accent_color", "background_color"},
        "profile.branding",
    )
    short_name = _string(branding["short_name"], "profile.branding.short_name")
    if len(short_name) > 40:
        raise CountyProfileValidationError("County branding short name is too long.")
    if not _LOGO_ASSET.fullmatch(
        _string(branding["logo_asset"], "profile.branding.logo_asset")
    ):
        raise CountyProfileValidationError("County branding logo must be synthetic.")
    for key in ("primary_color", "accent_color", "background_color"):
        if not _COLOR.fullmatch(_string(branding[key], f"profile.branding.{key}")):
            raise CountyProfileValidationError("County branding colors must be hex values.")

    agencies = profile["agencies"]
    if not isinstance(agencies, list) or not agencies:
        raise CountyProfileValidationError("profile.agencies must be a non-empty array.")
    for index, agency_value in enumerate(agencies):
        agency = _exact_keys(
            agency_value,
            {"id", "name", "abbreviation", "disciplines"},
            f"profile.agencies[{index}]",
        )
        agency_id = _string(agency["id"], f"profile.agencies[{index}].id")
        if not _IDENTIFIER.fullmatch(agency_id):
            raise CountyProfileValidationError("Agency ID must be a stable identifier.")
        agency_name = _string(agency["name"], f"profile.agencies[{index}].name")
        if len(agency_name) > 120:
            raise CountyProfileValidationError("Agency name is too long.")
        abbreviation = _string(
            agency["abbreviation"], f"profile.agencies[{index}].abbreviation"
        )
        if not _ABBREVIATION.fullmatch(abbreviation):
            raise CountyProfileValidationError("Agency abbreviation is invalid.")
        disciplines = frozenset(_unique_strings(
            agency["disciplines"],
            f"profile.agencies[{index}].disciplines",
            allow_empty=False,
        ))
        if not disciplines <= _DISCIPLINES:
            raise CountyProfileValidationError("Agency discipline is invalid.")

    mappings = profile["unit_status_mappings"]
    if not isinstance(mappings, Mapping) or not mappings:
        raise CountyProfileValidationError(
            "profile.unit_status_mappings must be a non-empty object."
        )
    for source, normalized in mappings.items():
        source_text = _string(source, "profile.unit_status_mappings key")
        normalized_text = _string(normalized, f"profile.unit_status_mappings.{source}")
        if not _UNIT_STATUS_KEY.fullmatch(source_text) or len(normalized_text) > 80:
            raise CountyProfileValidationError("Unit status mapping is invalid.")

    gis_sources = profile["gis_sources"]
    if not isinstance(gis_sources, list) or not gis_sources:
        raise CountyProfileValidationError("profile.gis_sources must be a non-empty array.")
    for index, source_value in enumerate(gis_sources):
        source = _exact_keys(
            source_value,
            {"id", "source_type", "data_class", "layers"},
            f"profile.gis_sources[{index}]",
        )
        source_id = _string(source["id"], f"profile.gis_sources[{index}].id")
        source_type = _string(
            source["source_type"], f"profile.gis_sources[{index}].source_type"
        )
        data_class = _string(
            source["data_class"], f"profile.gis_sources[{index}].data_class"
        )
        layers = _unique_strings(
            source["layers"],
            f"profile.gis_sources[{index}].layers",
            allow_empty=False,
        )
        if (
            not _IDENTIFIER.fullmatch(source_id)
            or source_type not in _GIS_SOURCE_TYPES
            or data_class not in _GIS_DATA_CLASSES
            or any(not _LAYER.fullmatch(layer) for layer in layers)
        ):
            raise CountyProfileValidationError("GIS source metadata is invalid.")

    identity = _exact_keys(
        profile["identity_federation"],
        {"provider", "trusted_tenant_claim", "metadata_only", "mfa_required"},
        "profile.identity_federation",
    )
    identity_provider = _string(
        identity["provider"], "profile.identity_federation.provider"
    )
    trusted_claim = _string(
        identity["trusted_tenant_claim"],
        "profile.identity_federation.trusted_tenant_claim",
    )
    if (
        not _IDENTIFIER.fullmatch(identity_provider)
        or not _CLAIM.fullmatch(trusted_claim)
        or identity["metadata_only"] is not True
        or not isinstance(identity["mfa_required"], bool)
    ):
        raise CountyProfileValidationError("Identity metadata flags are invalid.")

    retention = _exact_keys(
        profile["retention"],
        {"analytics_days", "audit_days", "export_days"},
        "profile.retention",
    )
    if any(
        not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3650
        for value in retention.values()
    ):
        raise CountyProfileValidationError("Retention values must be 1-3650 days.")

    ai_policy = _exact_keys(
        profile["ai_policy"],
        {
            "advisory_only",
            "protected_data_allowed",
            "inference_provider",
            "retrieval_provider",
            "allowed_tools",
        },
        "profile.ai_policy",
    )
    if ai_policy["advisory_only"] is not True or ai_policy["protected_data_allowed"] is not False:
        raise CountyProfileValidationError("AI must remain advisory and synthetic-data-only.")
    inference_provider = _string(
        ai_policy["inference_provider"], "profile.ai_policy.inference_provider"
    )
    retrieval_provider = _string(
        ai_policy["retrieval_provider"], "profile.ai_policy.retrieval_provider"
    )
    allowed_tools = frozenset(
        _unique_strings(ai_policy["allowed_tools"], "profile.ai_policy.allowed_tools")
    )
    if (
        not _IDENTIFIER.fullmatch(inference_provider)
        or not _IDENTIFIER.fullmatch(retrieval_provider)
        or not allowed_tools <= _AI_TOOLS
    ):
        raise CountyProfileValidationError("AI provider or tool metadata is invalid.")

    voice = _exact_keys(
        profile["voice_profile"],
        {"provider", "voice_id", "optional", "pronunciation_911"},
        "profile.voice_profile",
    )
    voice_provider = _string(voice["provider"], "profile.voice_profile.provider")
    voice_id = _string(voice["voice_id"], "profile.voice_profile.voice_id")
    if (
        not _IDENTIFIER.fullmatch(voice_provider)
        or not _IDENTIFIER.fullmatch(voice_id)
        or voice["optional"] is not True
        or voice["pronunciation_911"] != "nine one one"
    ):
        raise CountyProfileValidationError("Voice must be optional and pronounce 911 safely.")

    permissions = frozenset(
        _unique_strings(profile["alert_permissions"], "profile.alert_permissions")
    )
    if not permissions <= _ALERT_PERMISSIONS:
        raise CountyProfileValidationError("County profile contains an unsafe alert permission.")

    return profile


def county_profile_from_data(data: Any) -> CountyProfile:
    profile = validate_county_profile_data(data)
    return CountyProfile(
        tenant_id=profile["tenant_id"],
        display_name=profile["display_name"],
        timezone=profile["timezone"],
        region=profile["region"],
        cad_provider=profile["cad_provider"],
        capabilities=frozenset(profile["capabilities"]),
        modules=frozenset(profile["modules"]),
        branding=profile["branding"],
        agencies=tuple(profile["agencies"]),
        unit_status_mappings=profile["unit_status_mappings"],
        gis_sources=tuple(profile["gis_sources"]),
        identity_federation=profile["identity_federation"],
        retention=profile["retention"],
        ai_policy=profile["ai_policy"],
        voice_profile=profile["voice_profile"],
        alert_permissions=frozenset(profile["alert_permissions"]),
        profile_version=profile["profile_version"],
        contract_version=profile["contract_version"],
    )


def load_county_profile(path: str | Path) -> CountyProfile:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CountyProfileValidationError("County profile could not be loaded.") from exc
    return county_profile_from_data(data)


def load_builtin_county_profile(name: str) -> CountyProfile:
    if not _IDENTIFIER.fullmatch(name):
        raise CountyProfileValidationError("Built-in county profile name is invalid.")
    return load_county_profile(COUNTY_PROFILE_DIRECTORY / f"{name}.json")
