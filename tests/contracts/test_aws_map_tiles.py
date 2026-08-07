"""Network-free contracts for the signed Amazon Location tile proxy."""

import unittest
from io import BytesIO

from app.services.aws_map_tiles import (
    ALLOWED_TILESETS,
    MapTileUnavailable,
    fetch_map_tile,
    resolve_tileset,
    validate_tile_coordinates,
)


class _Client:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    def get_tile(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class ResolveTilesetTests(unittest.TestCase):
    def test_satellite_resolves_to_the_raster_tileset(self):
        self.assertEqual(resolve_tileset("satellite"), "raster.satellite")
        self.assertEqual(resolve_tileset("SATELLITE"), "raster.satellite")
        self.assertEqual(resolve_tileset("  satellite  "), "raster.satellite")

    def test_unapproved_or_vector_styles_are_rejected(self):
        for style in ("vector.basemap", "street", "", "raster.dem", "../etc"):
            with self.assertRaisesRegex(
                MapTileUnavailable, "map_tile_style_not_allowed"
            ):
                resolve_tileset(style)

    def test_only_satellite_is_currently_exposed(self):
        self.assertEqual(set(ALLOWED_TILESETS), {"satellite"})


class ValidateTileCoordinatesTests(unittest.TestCase):
    def test_accepts_in_range_coordinates(self):
        self.assertEqual(validate_tile_coordinates(0, 0, 0), (0, 0, 0))
        self.assertEqual(validate_tile_coordinates(3, 7, 5), (3, 7, 5))

    def test_rejects_out_of_range_zoom(self):
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_zoom_out_of_range"
        ):
            validate_tile_coordinates(-1, 0, 0)
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_zoom_out_of_range"
        ):
            validate_tile_coordinates(23, 0, 0)

    def test_rejects_coordinates_outside_the_zoom_levels_grid(self):
        # At zoom 3 there are 2**3 = 8 tiles per axis, indices 0-7.
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_coordinates_out_of_range"
        ):
            validate_tile_coordinates(3, 8, 0)
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_coordinates_out_of_range"
        ):
            validate_tile_coordinates(3, 0, -1)


class FetchMapTileTests(unittest.TestCase):
    def test_returns_bytes_and_content_type_on_success(self):
        client = _Client(
            response={"Blob": b"\x89PNG-fake-bytes", "ContentType": "image/png"}
        )
        payload, content_type = fetch_map_tile(
            client, style="satellite", z=5, x=3, y=2
        )
        self.assertEqual(payload, b"\x89PNG-fake-bytes")
        self.assertEqual(content_type, "image/png")
        self.assertEqual(
            client.calls, [{"Tileset": "raster.satellite", "Z": 5, "X": 3, "Y": 2}]
        )

    def test_reads_a_streaming_body_if_the_sdk_returns_one(self):
        client = _Client(
            response={"Blob": BytesIO(b"streamed"), "ContentType": "image/png"}
        )
        payload, _ = fetch_map_tile(client, style="satellite", z=1, x=0, y=0)
        self.assertEqual(payload, b"streamed")

    def test_defaults_content_type_when_the_provider_omits_it(self):
        client = _Client(response={"Blob": b"data"})
        _, content_type = fetch_map_tile(client, style="satellite", z=1, x=0, y=0)
        self.assertEqual(content_type, "image/png")

    def test_empty_blob_fails_closed(self):
        client = _Client(response={"Blob": b"", "ContentType": "image/png"})
        with self.assertRaisesRegex(MapTileUnavailable, "map_tile_empty"):
            fetch_map_tile(client, style="satellite", z=1, x=0, y=0)

    def test_provider_failure_is_sanitized(self):
        client = _Client(error=RuntimeError("provider payload must not escape"))
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_request_failed"
        ) as caught:
            fetch_map_tile(client, style="satellite", z=1, x=0, y=0)
        self.assertNotIn("provider payload", str(caught.exception))

    def test_rejects_before_ever_calling_the_provider(self):
        client = _Client(response={"Blob": b"unused"})
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_style_not_allowed"
        ):
            fetch_map_tile(client, style="vector.basemap", z=1, x=0, y=0)
        with self.assertRaisesRegex(
            MapTileUnavailable, "map_tile_zoom_out_of_range"
        ):
            fetch_map_tile(client, style="satellite", z=99, x=0, y=0)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
