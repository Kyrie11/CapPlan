from scripts.normalize_overpass_json import convert


def test_overpass_conversion_is_geojson_not_fake_osw() -> None:
    payload = {
        "generator": "Overpass API",
        "elements": [
            {"type": "node", "id": 1, "lon": -71.0, "lat": 42.0, "tags": {"entrance": "yes"}},
            {"type": "way", "id": 2, "geometry": [{"lon": -71.0, "lat": 42.0}, {"lon": -70.99, "lat": 42.01}], "tags": {"highway": "footway"}},
        ],
    }
    out = convert(payload, source_url="https://example.org/overpass")
    assert out["type"] == "FeatureCollection"
    assert len(out["features"]) == 2
    assert out["properties"]["schema_variant"] == "osm_geojson"
    assert out["properties"]["authoritative"] is False
    assert "opensidewalks" not in str(out).lower()
