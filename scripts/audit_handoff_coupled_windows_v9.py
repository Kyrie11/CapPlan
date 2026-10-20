#!/usr/bin/env python3
"""Conservative handoff window-intersection diagnostic (NOT a full POMDP).

Each case specifies one real/controlled location and finite event times.
Tests whether separately feasible passenger, vehicle and evidence conditions
have a shared executable boarding time. Unverified geometry and unknown
observation persistence remain UNKNOWN, never certified safe.

This is a falsification/interaction probe, not a new method or an empirical
real-world frequency estimator. It cannot model arbitrary rerouting or aid.
"""
from __future__ import annotations
import argparse
import json
import math
from collections import Counter
from pathlib import Path


def finite_nonnegative(v, label):
    if isinstance(v, bool):
        raise ValueError(f"{label}: boolean not numeric")
    out=float(v)
    if not math.isfinite(out) or out<0:
        raise ValueError(f"{label}: finite and nonnegative required")
    return out


def parse_case(line):
    d=json.loads(line)
    for k in ("episode_id", "passenger", "handoffs", "notice_times_s"):
        if k not in d:
            raise ValueError(f"case missing {k}")
    return d


def assess_option(passenger, handoff, notice):
    """Time ranges measured from same run-local origin, in seconds.

    Preconditions: an explicitly usable *joint* interface pose and occupancy
    window, a time-valid certificate, and a passenger functional contract.
    """
    required_p=("already_consumed_m","distance_budget_m","walking_speed_mps","ack_delay_s","wait_used_s","wait_budget_s")
    required_h=("distance_from_actionable_position_m","vehicle_arrival_earliest_s","vehicle_arrival_latest_s",
                "vehicle_max_dwell_s","boarding_duration_s","curb_window_start_s","curb_window_end_s")
    if any(k not in passenger or passenger[k] is None for k in required_p) or any(k not in handoff or handoff[k] is None for k in required_h):
        return {"option_id":handoff.get("option_id"),"decision":"UNKNOWN_MISSING_TIME_OR_CONTRACT"}
    p={k:finite_nonnegative(passenger[k],k) for k in required_p}
    h={k:finite_nonnegative(handoff[k],k) for k in required_h}
    notice=finite_nonnegative(notice,"notice")
    if p["walking_speed_mps"]<=0:raise ValueError("speed must be positive")
    if h["vehicle_arrival_latest_s"] < h["vehicle_arrival_earliest_s"]:
        raise ValueError("reversed vehicle window")
    if h["curb_window_end_s"] < h["curb_window_start_s"]:
        raise ValueError("reversed curb window")
    pedestrian_distance=p["already_consumed_m"] + h["distance_from_actionable_position_m"]
    ped_arrival=notice+p["ack_delay_s"]+h["distance_from_actionable_position_m"]/p["walking_speed_mps"]
    pedestrian_feasible=pedestrian_distance<=p["distance_budget_m"]+1e-9
    vehicle_feasible=(bool(handoff["vehicle_pose_executable"]) and
                      h["vehicle_arrival_earliest_s"]<=h["curb_window_end_s"])
    # Verified negative constraints are decisive even if another field is
    # unknown: the declared handoff option cannot work with excess walking.
    if not pedestrian_feasible or p["wait_used_s"] > p["wait_budget_s"] + 1e-9:
        return {"option_id":handoff.get("option_id"), "decision":"MODEL_WINDOW_EMPTY",
                "pedestrian_distance_feasible":pedestrian_feasible,
                "known_contract_failure":True}
    if handoff.get("interface_executable") is False or handoff.get("vehicle_pose_executable") is False:
        return {"option_id":handoff.get("option_id"),"decision":"MODEL_WINDOW_EMPTY",
                "known_interface_or_vehicle_pose_failure":True}
    if handoff.get("interface_executable") is None or handoff.get("vehicle_pose_executable") is None:
        return {"option_id":handoff.get("option_id"),"decision":"UNKNOWN_INTERFACE_OR_POSE"}
    interface_feasible=True
    evidence=handoff.get("evidence_certificate")
    # No future window *guarantee* from a generic timestamp, provenance tag,
    # or recent observation alone: observation could be stale when boarding.
    if not isinstance(evidence,dict) or evidence.get("guarantees_service_interval") is not True:
        return {"option_id":handoff.get("option_id"),"decision":"UNKNOWN_EVIDENCE_VALIDITY",
                "pedestrian_distance_feasible":pedestrian_feasible,
                "vehicle_pose_feasible":vehicle_feasible,"interface_feasible":interface_feasible}
    if "from_s" not in evidence or "through_s" not in evidence:
        return {"option_id":handoff.get("option_id"),"decision":"UNKNOWN_EVIDENCE_VALIDITY"}
    ev_from=finite_nonnegative(evidence["from_s"],"evidence.from_s")
    ev_until=finite_nonnegative(evidence["through_s"],"evidence.through_s")
    board=h["boarding_duration_s"]
    # Vehicle may time its arrival anywhere inside declared controllable
    # arrival interval, then wait at most max_dwell.
    earliest=max(ped_arrival,h["vehicle_arrival_earliest_s"],h["curb_window_start_s"],ev_from)
    latest=min(h["vehicle_arrival_latest_s"]+h["vehicle_max_dwell_s"],
               h["curb_window_end_s"]-board, ev_until-board,
               ped_arrival+max(0,p["wait_budget_s"]-p["wait_used_s"]))
    # Do not count walking acknowledgement as waiting if that was not
    # specified. The separate wait-used clock is authoritative.
    joint=pedestrian_feasible and vehicle_feasible and interface_feasible and p["wait_used_s"]<=p["wait_budget_s"]+1e-9 and earliest<=latest+1e-9
    evidence_marginal=ev_until-ev_from>=board-1e-9
    vehicle_marginal=h["vehicle_arrival_latest_s"]+h["vehicle_max_dwell_s"]>=h["vehicle_arrival_earliest_s"]
    all_marginals=pedestrian_feasible and vehicle_feasible and interface_feasible and evidence_marginal and vehicle_marginal
    return {
        "option_id":handoff.get("option_id"),
        "decision":"MODEL_WINDOW_EXISTS" if joint else "MODEL_WINDOW_EMPTY",
        "pedestrian_distance_feasible":pedestrian_feasible,
        "vehicle_pose_feasible":vehicle_feasible,
        "interface_feasible":interface_feasible,
        "evidence_window_individually_long_enough":evidence_marginal,
        "vehicle_window_nonempty":vehicle_marginal,
        "all_marginals_feasible":all_marginals,
        "joint_window_empty_despite_feasible_marginals":bool(all_marginals and not joint),
        "passenger_arrival_s":round(ped_arrival,4),
        "consumed_total_m":round(pedestrian_distance,4),
        "earliest_boarding_start_s":round(earliest,4),
        "last_admissible_boarding_start_s":round(latest,4),
        "time_window_slack_s":round(latest-earliest,4),
        "margin_distance_m":round(p["distance_budget_m"]-pedestrian_distance,4),
        "independent_physical_verification":False,
    }


def assess_case(case):
    rows=[]
    snapshots=case.get("passenger_state_by_notice_s")
    for t in case["notice_times_s"]:
        # An experiment about *actual passenger progress* MUST supply the
        # measured/simulated actionable position for each notice. In its
        # absence this is a static-geometry timing-only sensitivity.
        state=dict(case["passenger"])
        options=[dict(h) for h in case["handoffs"]]
        position_mode="STATIC_ACTIONABLE_POSITION_TIMING_ONLY"
        if snapshots is not None:
            snap=snapshots.get(str(t),snapshots.get(str(float(t))))
            if snap is None:
                rows.append({"episode_id":case["episode_id"],"notice_issued_at_s":t,
                             "decision":"UNKNOWN_NO_ACTIONABLE_POSITION",
                             "options":[],"can_claim_real_handoff":False,
                             "passenger_progress_mode":"MISSING_SNAPSHOT"})
                continue
            state.update({k:snap[k] for k in ("already_consumed_m","wait_used_s") if k in snap})
            for h in options:
                if h["option_id"] not in snap.get("distance_to_option_m",{}):
                    raise ValueError("missing per-option distance in actionable-position snapshot")
                h["distance_from_actionable_position_m"]=snap["distance_to_option_m"][h["option_id"]]
            position_mode="EXPLICIT_ACTIONABLE_POSITION_SNAPSHOT"
        outcomes=[assess_option(state,h,t) for h in options]
        known=[o for o in outcomes if o["decision"] in ("MODEL_WINDOW_EXISTS","MODEL_WINDOW_EMPTY")]
        exists=any(o["decision"]=="MODEL_WINDOW_EXISTS" for o in known)
        incomplete=len(known)!=len(outcomes)
        # No feasible known candidate does not prove absence if other candidates are UNKNOWN.
        tri="MODEL_CANDIDATE_EXISTS" if exists else "UNKNOWN" if incomplete else "NO_WINDOW_IN_DECLARED_OPTIONS"
        rows.append({"episode_id":case["episode_id"],"notice_issued_at_s":t,
                     "decision":tri,"options":outcomes,
                     "case_provenance":case.get("case_provenance","UNDECLARED"),
                     "declared_action_space_only":True,
                     "passenger_progress_mode":position_mode,
                     "can_claim_real_handoff":False})
    return rows


def run(cases):
    rows=[]
    for case in cases:
        rows.extend(assess_case(case))
    counts=Counter(row["decision"] for row in rows)
    counts["marginal_nonseparability_witnesses"] = sum(
        int(o.get("joint_window_empty_despite_feasible_marginals",False)) for r in rows for o in r["options"])
    return rows, {"n_cases":len(cases),"n_case_time_rows":len(rows),"counts":dict(counts),
                  "evidence_scope":"Only declared time/pose/route options; no future hidden-world causal policy proof",
                  "real_frequency_estimated":False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases",required=True);p.add_argument("--output",required=True)
    a=p.parse_args()
    cases=[]
    for line in Path(a.cases).read_text(encoding="utf-8").splitlines():
        if line.strip():cases.append(parse_case(line))
    rows,summary=run(cases)
    path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in rows),encoding="utf-8")
    path.with_suffix(".summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    main()
