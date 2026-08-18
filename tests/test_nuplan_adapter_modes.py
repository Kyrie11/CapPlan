import pytest
from capplan.data.nuplan_adapter import NuPlanAdapter


def test_nuplan_mode_raises_when_devkit_missing_or_paths_bad(tmp_path):
    with pytest.raises(RuntimeError):
        NuPlanAdapter(scene_source="nuplan", data_root=str(tmp_path/"data"), map_root=str(tmp_path/"maps"), db_files=str(tmp_path/"db"), map_version="v")


def test_synthetic_mode_marks_source_synthetic_not_nuplan():
    rec = next(iter(NuPlanAdapter(scene_source="synthetic", seed=1).iter_scenarios(1)))
    assert rec.scene.source == "synthetic"
    assert rec.episode.scene_source == "synthetic"


class _FakeTimePoint:
    def __init__(self, time_us):
        self.time_us = time_us


class _FakeEgo:
    def __init__(self, time_us=None):
        if time_us is not None:
            self.time_point = _FakeTimePoint(time_us)


def test_ego_time_seconds_prefers_official_time_point_time_us():
    t_s, source, is_absolute = NuPlanAdapter._ego_time_seconds(_FakeEgo(1_625_000_123_456_789), 7.0)
    assert t_s == pytest.approx(1_625_000_123.456789)
    assert source == "ego_state.time_point.time_us"
    assert is_absolute is True


def test_ego_time_seconds_iteration_fallback_is_explicit_not_absolute():
    t_s, source, is_absolute = NuPlanAdapter._ego_time_seconds(_FakeEgo(), 7.0)
    assert t_s == 7.0
    assert source == "iteration_fallback"
    assert is_absolute is False
