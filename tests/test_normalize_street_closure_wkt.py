from argparse import Namespace

from scripts.normalize_accessibility_evidence import geometry, normalize


def _args():
    return Namespace(width_unit="unknown", reveal_unit="unknown", slope_unit="unknown")


def test_wprdc_street_closure_linestring_wkt_is_preserved_as_geometry():
    row = {
        "id": "closure-1",
        "wkt": "LINESTRING (-80.0000 40.4400, -79.9990 40.4410)",
        "start_date": "2020-05-01",
        "end_date": "2020-05-02",
    }
    geom = geometry(row)
    assert geom == {
        "type": "LineString",
        "coordinates": [[-80.0, 40.44], [-79.999, 40.441]],
    }
    feat = normalize("pittsburgh_street_closure", row, 0, "wprdc_street_closures", _args())
    assert feat["geometry"]["type"] == "LineString"
    assert feat["kind"] == "temporary_street_closure"
