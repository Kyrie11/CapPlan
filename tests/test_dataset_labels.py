import subprocess
import sys
from pathlib import Path

from capplan.utils.serialization import read_jsonl


def test_dataset_builder_outputs_required_files(tmp_path):
    out = tmp_path / "dataset"
    subprocess.check_call([sys.executable, "scripts/build_dataset.py", "--max_scenarios", "1", "--num_contracts_per_scene", "1", "--output_dir", str(out)], cwd=Path(__file__).resolve().parents[1])
    for name in ["episodes.jsonl", "pudo_anchors.jsonl", "vehicle_interfaces.jsonl", "capability_contracts.jsonl", "candidate_transitions.jsonl", "resource_labels.jsonl", "skeleton_labels.jsonl"]:
        assert (out / name).exists()
    assert read_jsonl(out / "candidate_transitions.jsonl")
