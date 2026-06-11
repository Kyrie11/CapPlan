from capplan.data.schemas import Pose2D, SceneRecord, PUDOAnchor, RequirementGroup, to_dict, scene_from_dict, pudo_from_dict


def test_schema_round_trip_scene_record():
    s = SceneRecord("e", "synthetic", "mini", scenario_token="tok", log_name="log", map_name="map", map_version="v", initial_ego_pose=Pose2D(1, 2, 0.3), mission_goal=Pose2D(5, 6), route_roadblock_ids=["rb"], route_corridor={"length_m": 10})
    s2 = scene_from_dict(to_dict(s))
    assert s2.source == "synthetic"
    assert s2.initial_ego_pose.x == 1
    assert s2.route_roadblock_ids == ["rb"]


def test_schema_round_trip_pudo_anchor_with_road_and_ped_links():
    p = PUDOAnchor("p0", "e", "pickup_dropoff", Pose2D(1, 2), Pose2D(1, 0), "right", True, roadblock_id="rb", lane_id="ln", adjacent_ped_node_id="n")
    p2 = pudo_from_dict(to_dict(p))
    assert p2.roadblock_id == "rb"
    assert p2.adjacent_ped_node_id == "n"
    assert p2.x == 1


def test_requirement_group_any_of_ramp_or_lift():
    g = RequirementGroup("g", ["board"], "any_of", ["ramp", "lift"])
    assert g.logic == "any_of"
    assert g.clause_ids == ["ramp", "lift"]
