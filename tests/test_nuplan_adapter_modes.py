import pytest
from capplan.data.nuplan_adapter import NuPlanAdapter


def test_nuplan_mode_raises_when_devkit_missing_or_paths_bad(tmp_path):
    with pytest.raises(RuntimeError):
        NuPlanAdapter(scene_source="nuplan", data_root=str(tmp_path/"data"), map_root=str(tmp_path/"maps"), db_files=str(tmp_path/"db"), map_version="v")


def test_synthetic_mode_marks_source_synthetic_not_nuplan():
    rec = next(iter(NuPlanAdapter(scene_source="synthetic", seed=1).iter_scenarios(1)))
    assert rec.scene.source == "synthetic"
    assert rec.episode.scene_source == "synthetic"
