"""Finite-horizon, observation-causal handoff *research* oracle.

Two matched solvers enumerate the SAME actions, worlds, observations, directed
pedestrian edges and resource ledgers. They differ only in validating a full
boarding interval (independent integer-tick enumeration vs interval algebra).
This is NOT a reproduction of any published POMDP shield, a continuous motion
checker, or evidence that a real passenger can board at a nuPlan curb.

All time windows are integer-second, HALF-OPEN [start, end). An action that
boards for d seconds at t requires coverage of every tick in [t, t+d).
UNKNOWN required physical inputs return UNKNOWN_INPUT, never "infeasible".
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from collections import Counter
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any


class InputUnknown(ValueError):
    """A missing/invalid required model field; not a physical impossibility."""


class SearchLimit(RuntimeError):
    """The finite search was cut off; do not claim no policy."""


@dataclass(frozen=True)
class State:
    node: str
    t: int
    spent_m: float
    waited_s: int
    vehicle_target: str | None
    vehicle_ready_s: int
    worlds: tuple[str, ...]


def _integer(value: Any, label: str, *, lo: int = 0) -> int:
    if type(value) is not int or value < lo:
        raise InputUnknown(f"{label}: expected integer >= {lo}, got {value!r}")
    return value


def _positive_float(value: Any, label: str, *, nonnegative=False) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or (
        value < 0 if nonnegative else value <= 0
    ):
        raise InputUnknown(f"{label}: invalid nonnegative/positive finite number")
    return float(value)


def _windows(value: Any, label: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        raise InputUnknown(f"{label}: missing list of explicit windows")
    out = []
    for idx, span in enumerate(value):
        if not isinstance(span, list) or len(span) != 2:
            raise InputUnknown(f"{label}[{idx}]: expected [begin,end]")
        lo, hi = (_integer(span[0], label), _integer(span[1], label))
        if hi <= lo:
            raise InputUnknown(f"{label}[{idx}]: interval must be nonempty")
        out.append((lo, hi))
    return tuple(sorted(out))


def _join(ranges: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    result = []
    for a, b in sorted(ranges):
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return tuple(result)


def _covered(reference: tuple[tuple[int, int], ...], t: int, duration: int) -> bool:
    """Fully independent grid predicate; checks every model tick."""
    for tick in range(t, t + duration):
        if not any(start <= tick < end for start, end in reference):
            return False
    return True


def _interval_covers(reference: tuple[tuple[int, int], ...], t: int, duration: int) -> bool:
    """Full interval coverage, allowing adjacent/overlapping windows to merge."""
    return any(start <= t and t + duration <= end for start, end in _join(reference))


class HandoffModel:
    def __init__(self, case: dict):
        self.case = case
        self.name = str(case.get("case_id", "unnamed"))
        if case.get("claim_scope") not in ("CONTROLLED_SYNTHETIC_ONLY", "INDEPENDENTLY_ATTESTED_MODEL"):
            raise InputUnknown("claim_scope: must explicitly distinguish controlled from audited")
        self.scope = case["claim_scope"]
        self.horizon = _integer(case.get("horizon_s"), "horizon_s", lo=1)
        if self.horizon > 600:
            raise InputUnknown("horizon_s>600 is not supported in this reference prototype")
        capability = case.get("capability") or {}
        self.max_distance = _positive_float(capability.get("max_distance_m"), "max_distance_m")
        self.max_wait = _integer(capability.get("max_wait_s"), "max_wait_s")
        self.speed = _positive_float(capability.get("speed_mps"), "speed_mps")
        opts = case.get("options")
        if not isinstance(opts, dict) or not (1 <= len(opts) <= 8):
            raise InputUnknown("options: need 1..8 explicit choices")
        self.options = {}
        for target, payload in opts.items():
            if not isinstance(payload, dict) or not isinstance(target, str):
                raise InputUnknown("options: keys/records must be explicit")
            self.options[target] = {
                "drive_eta_s": _integer(payload.get("drive_eta_s"), "drive_eta_s"),
                "ack_s": _integer(payload.get("ack_s"), "ack_s", lo=1),
                "verify_s": _integer(payload.get("verify_s"), "verify_s", lo=1),
                "boarding_s": _integer(payload.get("boarding_s"), "boarding_s", lo=1),
            }
        self.edges = {}
        edge_records = case.get("directed_edges_m")
        if not isinstance(edge_records, dict):
            raise InputUnknown("directed_edges_m: missing; do not infer reverse paths")
        for src, destinations in edge_records.items():
            if not isinstance(destinations, dict):
                raise InputUnknown("directed_edges_m: each source needs an explicit mapping")
            self.edges[src] = {dst: _positive_float(v, f"edge:{src}->{dst}")
                               for dst, v in destinations.items()}
        world_records = case.get("worlds")
        if not isinstance(world_records, list) or not world_records:
            raise InputUnknown("worlds: missing")
        self.worlds = {}
        for world in world_records:
            if not isinstance(world, dict) or not isinstance(world.get("id"), str):
                raise InputUnknown("world entry: invalid or missing id")
            wid = world["id"]
            if wid in self.worlds:
                raise InputUnknown("duplicate world id")
            entries = world.get("handoff_windows")
            if not isinstance(entries, dict):
                raise InputUnknown(f"{wid}: handoff_windows missing")
            site_truth = {}
            for name in self.options:
                v = entries.get(name)
                if not isinstance(v, dict):
                    raise InputUnknown(f"{wid}:{name}: no handoff evidence")
                site_truth[name] = {k: _windows(v.get(k), f"{wid}:{name}:{k}")
                                    for k in ("stop", "evidence", "interface", "open")}
            self.worlds[wid] = site_truth
        initial = case.get("initial") or {}
        pos = initial.get("node")
        if not isinstance(pos, str) or not pos:
            raise InputUnknown("initial.node missing")
        spent = _positive_float(initial.get("consumed_m"), "initial.consumed_m", nonnegative=True)
        waited = _integer(initial.get("waited_s"), "initial.waited_s")
        t = _integer(initial.get("time_s"), "initial.time_s")
        if spent > self.max_distance or waited > self.max_wait:
            raise InputUnknown("initial ledger exceeds the declared capability contract")
        self.initial = State(pos, t, round(spent, 6), waited, None, 0,
                             tuple(sorted(self.worlds)))
        # Safe continuation is *never* available by default. A real-world
        # attestable guarantee must be a separate evidence source.
        if case.get("verified_safe_continuation") not in (None, False):
            raise InputUnknown("verified_safe_continuation needs independent protocol; unsupported")

    def board(self, state: State, interval: bool, count: Counter) -> bool:
        target = state.node
        if target not in self.options or state.vehicle_target != target:
            return False
        if state.t < state.vehicle_ready_s:
            return False
        duration = self.options[target]["boarding_s"]
        if state.t + duration > self.horizon:
            return False
        for wid in state.worlds:
            for part in ("stop", "evidence", "interface", "open"):
                count["boarding_predicate_calls"] += 1
                windows = self.worlds[wid][target][part]
                good = (_interval_covers if interval else _covered)(windows, state.t, duration)
                if not good:
                    return False
        return True

    def actions(self, state: State) -> list[str]:
        if state.t >= self.horizon:
            return []
        result = []
        if state.node in self.options and state.vehicle_target == state.node:
            result.append("board")
        # Passenger movement and vehicle dispatch happen concurrently; if the
        # vehicle is already heading there, do not restart its route clock.
        for target in self.options:
            if target != state.node and target in self.edges.get(state.node, {}):
                result.append("guide:" + target)
        for target in self.options:
            if state.vehicle_target != target:
                result.append("dispatch:" + target)
        for target in self.options:
            result.append("verify:" + target)
        result.append("wait")
        return result

    def transition(self, state: State, action: str):
        """Observable successor groups. None = disallowed action."""
        if action == "wait":
            if state.t + 1 > self.horizon or state.waited_s + 1 > self.max_wait:
                return None
            return [("elapsed", State(state.node, state.t+1, state.spent_m,
                       state.waited_s+1, state.vehicle_target, state.vehicle_ready_s, state.worlds))]
        kind, _, target = action.partition(":")
        if target not in self.options:
            return None
        spec = self.options[target]
        if kind == "guide":
            length = self.edges.get(state.node, {}).get(target)
            if length is None or target == state.node:
                return None
            spent = round(state.spent_m + length, 6)
            arrived = state.t + spec["ack_s"] + math.ceil(length/self.speed)
            waited = state.waited_s + spec["ack_s"]
            if spent > self.max_distance + 1e-7 or waited > self.max_wait or arrived > self.horizon:
                return None
            ready = state.vehicle_ready_s if state.vehicle_target == target else state.t + spec["drive_eta_s"]
            return [("arrived", State(target, arrived, spent, waited, target, ready, state.worlds))]
        if kind == "dispatch":
            if state.vehicle_target == target:
                return None
            arrived = state.t + spec["ack_s"]
            if arrived > self.horizon or state.waited_s + spec["ack_s"] > self.max_wait:
                return None
            return [("ack", State(state.node, arrived, state.spent_m,
                     state.waited_s + spec["ack_s"], target,
                     state.t + spec["drive_eta_s"], state.worlds))]
        if kind == "verify":
            arrived = state.t + spec["verify_s"]
            if arrived > self.horizon or state.waited_s + spec["verify_s"] > self.max_wait:
                return None
            groups: dict[str, list[str]] = {}
            for wid in state.worlds:
                truth = self.worlds[wid][target]
                if not any(lo <= arrived < hi for lo,hi in truth["evidence"]):
                    observation = "UNKNOWN"
                elif any(lo <= arrived < hi for lo,hi in truth["open"]):
                    observation = "OPEN"
                else:
                    observation = "BLOCKED"
                groups.setdefault(observation, []).append(wid)
            return [(label, State(state.node, arrived, state.spent_m,
                      state.waited_s+spec["verify_s"], state.vehicle_target,
                      state.vehicle_ready_s, tuple(group)))
                    for label, group in sorted(groups.items())]
        return None


class ExactShield:
    """Memoized AND/OR: existential action, universal observation outcomes."""
    def __init__(self, model: HandoffModel, *, interval: bool, max_states: int = 20000):
        self.model = model
        self.interval = interval
        self.max_states = max_states
        self.stats = Counter()
        self.policy = {}

    def solve(self) -> dict:
        start = perf_counter()
        model = self.model

        @lru_cache(maxsize=None)
        def winning(state: State) -> bool:
            self.stats["expanded_states"] += 1
            if self.stats["expanded_states"] > self.max_states:
                raise SearchLimit(f"state cap {self.max_states} reached")
            for action in model.actions(state):
                self.stats["action_tests"] += 1
                if action == "board":
                    if model.board(state, self.interval, self.stats):
                        self.policy[state] = (action, ())
                        return True
                    continue
                next_states = model.transition(state, action)
                if next_states and all(winning(nxt) for _, nxt in next_states):
                    self.policy[state] = (action, tuple((key,nxt) for key,nxt in next_states))
                    return True
            return False

        try:
            all_safe=[]
            for action in model.actions(model.initial):
                if action == "board":
                    viable = model.board(model.initial, self.interval, self.stats)
                else:
                    nxt = model.transition(model.initial, action)
                    viable = bool(nxt) and all(winning(s) for _,s in nxt)
                if viable:
                    all_safe.append(action)
            status = "SAFE_ACTIONS_FOUND" if all_safe else "NO_POLICY_WITHIN_DECLARED_FINITE_MODEL"
        except (SearchLimit, RecursionError):
            status = "UNKNOWN_COMPUTATION_LIMIT"
            all_safe = None
        elapsed = (perf_counter()-start)*1000
        return {"case_id":model.name,"status":status,"safe_initial_actions":all_safe,
                "claim_scope":model.scope,"semantics":"ALL_WORLDS_ONE_OBSERVATION_CAUSAL_POLICY_MANY_VERIFY_STEPS",
                "solver":"interval_board" if self.interval else "independent_integer_grid_board",
                "not_published_shield":True,"not_vehicle_motion_or_real_handoff_verified":True,
                "elapsed_ms":round(elapsed,4),"stats":dict(self.stats)}


def compare(case: dict, *, max_states: int = 20000) -> dict:
    """Refuse to label missing physical evidence as a genuine negative."""
    try:
        model=HandoffModel(case)
    except (InputUnknown, TypeError, ValueError) as e:
        return {"case_id":case.get("case_id"),"status":"UNKNOWN_INPUT",
                "reason":str(e),"safe_actions_agree":None,
                "no_real_world_claim":True}
    grid=ExactShield(model,interval=False,max_states=max_states).solve()
    interval=ExactShield(model,interval=True,max_states=max_states).solve()
    if grid["safe_initial_actions"] is None or interval["safe_initial_actions"] is None:
        agreement = None
    else:
        agreement = (set(grid["safe_initial_actions"]) == set(interval["safe_initial_actions"]))
    return {"case_id":model.name,"status":"COMPARED" if agreement is not None else "UNKNOWN_COMPUTATION_LIMIT",
            "safe_actions_agree":agreement,"grid":grid,"interval":interval,
            "scientific_scope":"CONTROLLED_FINITE_HORIZON_MULTI_OBSERVATION_COMPARISON",
            "not_published_shield":True,"no_real_world_claim":True}


def compare_jsonl(cases: Path, output: Path, *, max_states=20000, max_cases=0) -> dict:
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    counts=Counter();total=0;failures=[]
    with Path(cases).open(encoding="utf-8") as src,output.open("w",encoding="utf-8") as dst:
        for line in src:
            if not line.strip():continue
            if max_cases and total>=max_cases:break
            total+=1
            r=compare(json.loads(line),max_states=max_states)
            dst.write(json.dumps(r,ensure_ascii=False)+"\n")
            counts[r["status"]]+=1
            counts["agreements"]+=int(r["safe_actions_agree"] is True)
            counts["disagreements"]+=int(r["safe_actions_agree"] is False)
            if r["safe_actions_agree"] is False:failures.append(r["case_id"])
    result={"cases":total,"counts":dict(counts),"disagreement_examples":failures[:20],
            "scope":"CONTROLLED_MULTI_OBSERVATION_NOT_PUBLISHED_SHIELD",
            "not_independent_real_physical_evidence":True}
    output.with_suffix(".summary.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return result
