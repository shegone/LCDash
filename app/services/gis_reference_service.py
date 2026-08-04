"""Private, minimized GIS reference layers for the operations map.

The original GIS archives never pass through this service.  A separate import
step writes reviewed GeoJSON files to ``GIS_REFERENCE_DIR``; this module then
allows only the named layers and the few display properties the map needs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config.settings import settings


REFERENCE_LAYERS: dict[str, dict[str, Any]] = {
    "county": {
        "file": "county_boundary.geojson",
        "label": "County boundary",
        "default_visible": True,
        "properties": {"name": "County"},
    },
    "psap": {
        "file": "psap_boundary.geojson",
        "label": "PSAP coverage",
        "default_visible": True,
        "properties": {"name": "PSAPName"},
    },
    "municipalities": {
        "file": "municipalities.geojson",
        "label": "Municipal boundaries",
        "default_visible": False,
        "properties": {"name": "IncMuni"},
    },
    "provisioning": {
        "file": "provisioning_boundary.geojson",
        "label": "Provisioning boundary",
        "default_visible": False,
        "properties": {"name": "PrvBndNm", "type": "PrvBndTp"},
    },
    "esb-fire": {
        "file": "esb_fire.geojson",
        "label": "Fire response areas",
        "default_visible": False,
        "properties": {"agency": "AgencyName"},
    },
    "esb-ems": {
        "file": "esb_ems.geojson",
        "label": "EMS response areas",
        "default_visible": False,
        "properties": {"agency": "AgencyName"},
    },
    "esb-law": {
        "file": "esb_law.geojson",
        "label": "Law response areas",
        "default_visible": False,
        "properties": {"agency": "AgencyName"},
    },
    "roads": {
        "file": "roads.geojson",
        "label": "Road network",
        "default_visible": True,
        "properties": {"name": "LSt_Name", "class": "RoadClass"},
    },
}


def _reference_path(layer: str) -> Path:
    return Path(settings.gis_reference_dir) / REFERENCE_LAYERS[layer]["file"]


def available_reference_layers() -> list[dict[str, str | bool]]:
    """Return a catalog only; no geometry or raw GIS metadata is exposed."""
    available = []
    for layer, definition in REFERENCE_LAYERS.items():
        if _reference_path(layer).is_file():
            available.append(
                {
                    "id": layer,
                    "label": definition["label"],
                    "default_visible": definition["default_visible"],
                }
            )
    return available


def _minimized_feature(feature: dict, definition: dict[str, Any]) -> dict | None:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or not geometry.get("type") or "coordinates" not in geometry:
        return None

    source_properties = feature.get("properties")
    source_properties = source_properties if isinstance(source_properties, dict) else {}
    properties = {
        target: str(source_properties.get(source, "")).strip()
        for target, source in definition["properties"].items()
        if str(source_properties.get(source, "")).strip()
    }
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": properties,
    }


def get_reference_layer(layer: str) -> dict | None:
    """Load a reviewed static layer, removing all unapproved source fields."""
    if layer not in REFERENCE_LAYERS:
        return None

    path = _reference_path(layer)
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    source_features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(source_features, list):
        return None

    features = [
        minimized
        for feature in source_features
        if isinstance(feature, dict)
        for minimized in [_minimized_feature(feature, REFERENCE_LAYERS[layer])]
        if minimized is not None
    ]
    return {
        "type": "FeatureCollection",
        "layer": layer,
        "features": features,
    }
