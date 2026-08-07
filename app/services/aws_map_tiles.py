"""Server-signed Amazon Location map tiles.

Amazon Location Maps v2 requires SigV4-signed requests, which a browser
tile layer cannot produce without exposing credentials. The application
therefore proxies tiles: the browser requests a plain tile URL, and this
module signs the upstream call with the ECS task role and returns the
bytes. No credential ever reaches the client.

Only raster tilesets are exposed. ``vector.basemap`` is deliberately not
offered here because the map client renders raster tiles; serving vector
tiles would require a different client renderer entirely.

Nothing here performs a network call at import time; the client is created
lazily on first request, mirroring the other cloud provider adapters.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any, Protocol

import boto3


APPROVED_REGION = "us-east-1"

# Tileset identifiers are interpolated into an AWS API call, so only these
# exact reviewed values are ever accepted. `raster.satellite` is the aerial
# imagery layer; `raster.dem` is terrain elevation. Vector tilesets are
# excluded because this application's map renders raster tiles.
ALLOWED_TILESETS = {
    "satellite": "raster.satellite",
}

# Web Mercator: zoom 0-22 is the practical tile range, and at zoom z there
# are 2**z tiles per axis. Bounds are enforced so a malformed or hostile
# request cannot be forwarded upstream.
MAX_TILE_ZOOM = 22


class GeoMapsClient(Protocol):
    def get_tile(self, **kwargs: Any) -> dict[str, Any]: ...


class LazyGeoMapsClient:
    """Create the Amazon Location Maps client on first tile request."""

    def __init__(self, *, region_name: str = APPROVED_REGION) -> None:
        self._region_name = region_name

    @cached_property
    def _client(self):
        return boto3.client("geo-maps", region_name=self._region_name)

    def get_tile(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.get_tile(**kwargs)


class MapTileUnavailable(RuntimeError):
    """Sanitized failure category; never carries a provider payload."""


def resolve_tileset(style: str) -> str:
    """Map a public style name onto an approved AWS tileset identifier."""
    tileset = ALLOWED_TILESETS.get(str(style or "").strip().lower())
    if tileset is None:
        raise MapTileUnavailable("map_tile_style_not_allowed")
    return tileset


def validate_tile_coordinates(z: int, x: int, y: int) -> tuple[int, int, int]:
    """Reject out-of-range tile coordinates before any upstream call."""
    if not 0 <= z <= MAX_TILE_ZOOM:
        raise MapTileUnavailable("map_tile_zoom_out_of_range")
    limit = 1 << z
    if not 0 <= x < limit or not 0 <= y < limit:
        raise MapTileUnavailable("map_tile_coordinates_out_of_range")
    return z, x, y


def fetch_map_tile(
    client: GeoMapsClient, *, style: str, z: int, x: int, y: int
) -> tuple[bytes, str]:
    """Return one signed map tile as ``(bytes, content_type)``."""
    tileset = resolve_tileset(style)
    zoom, tile_x, tile_y = validate_tile_coordinates(z, x, y)
    try:
        response = client.get_tile(
            Tileset=tileset, Z=zoom, X=tile_x, Y=tile_y
        )
    except Exception as exc:  # provider payloads never leave this frame
        raise MapTileUnavailable("map_tile_request_failed") from exc

    blob = response.get("Blob")
    payload = blob.read() if hasattr(blob, "read") else blob
    if not payload:
        raise MapTileUnavailable("map_tile_empty")
    content_type = str(response.get("ContentType") or "image/png")
    return bytes(payload), content_type
