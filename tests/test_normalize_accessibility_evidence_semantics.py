import argparse
import importlib.util
from pathlib import Path


def _module():
    spec = importlib.util.spec_from_file_location("normalize_accessibility_evidence", Path("scripts/normalize_accessibility_evidence.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_boston_ramp_does_not_turn_sidewalk_width_into_deployment_clearance():
    mod = _module()
    args = argparse.Namespace(width_unit="feet", reveal_unit="unknown", slope_unit="unknown")
    row = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-71.0, 42.0]}, "properties": {"OBJECTID": 1, "SWK_WIDTH": "6", "REVEAL": "0", "APRON_SL": "8"}}
    out = mod.normalize("boston_ramp", row, 0, "Boston Ramp Inventory", args)
    assert round(out["sidewalk_width_m"], 4) == 1.8288
    assert out["deployment_clearance_m"] is None
    assert out["curb_height_m"] is None
    assert out["ramp_slope"] is None
    assert out["requires_manual_deployment_clearance_audit"] is True


def test_pittsburgh_address_point_stays_proxy():
    mod = _module()
    args = argparse.Namespace(width_unit="unknown", reveal_unit="unknown", slope_unit="unknown")
    row = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-79.95, 40.44]}, "properties": {"ADDRESS_ID": 10, "FULL_ADDRE": "1 TEST ST"}}
    out = mod.normalize("pittsburgh_address_point", row, 0, "Allegheny Address Points", args)
    assert out["properties"]["kind"] == "entrance_proxy"
    assert out["properties"]["is_proxy"] is True
