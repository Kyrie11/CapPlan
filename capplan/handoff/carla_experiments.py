"""Vehicle-only CARLA five-way controls to isolate redirect vs blocker effects.

The five modes are:
  clear_A, blocked_A, clear_B, clear_A_redirect_B, blocked_A_redirect_B.
The historic folder alias `redirect_B` is used for the fifth mode.
A stopped BasicAgent within a Town target radius is NEVER a legal PUDO or
passenger boarding witness. Sensor callback count != independent collisions.
"""
from __future__ import annotations
from pathlib import Path
import json
from collections import Counter
from capplan.handoff.carla_calibration_v12 import _load_runs
from capplan.handoff.research import _read_jsonl

FOLDERS={'clear_A':'clear_A','blocked_A':'blocked_A','clear_B':'clear_B',
         'clear_A_redirect_B':'clear_A_redirect_B','blocked_A_redirect_B':'redirect_B'}


def audit(root: Path) -> dict:
    root=Path(root)
    validated={mode:{v['trial']:v for v in _load_runs(root/folder)}
               for mode,folder in FOLDERS.items()}
    raw={mode:{p.parent.name:json.loads(p.read_text(encoding='utf-8'))
               for p in (root/folder).glob('*/execution_report.json')}
         for mode,folder in FOLDERS.items()}
    trials=sorted(set().union(*(set(v) for v in raw.values())))
    counts=Counter(); rows=[]
    for trial in trials:
        if any(trial not in raw[m] or trial not in validated[m] for m in FOLDERS):
            counts['incomplete_fiveway_controls']+=1
            rows.append({'trial':trial,'status':'UNKNOWN_MISSING_FIVE_WAY_CONTROL',
                        'missing':[m for m in FOLDERS if trial not in raw[m] or trial not in validated[m]]})
            continue
        reports={m:raw[m][trial] for m in FOLDERS}
        checks={m:validated[m][trial] for m in FOLDERS}
        sig={checks[m].get('experiment_config_signature') for m in FOLDERS}
        same_configuration=len(sig)==1 and None not in sig
        same_geometry=all(reports[m].get('carla_locations') == reports['clear_A'].get('carla_locations')
                          for m in FOLDERS)
        targets={}
        for m in FOLDERS:
            path=root/FOLDERS[m]/trial/'trajectory.jsonl'
            history=[x.get('target') for x in _read_jsonl(path)] if path.exists() else []
            targets[m]=history
        mode_success={}
        for m in FOLDERS:
            v=checks[m]
            goal=('B' if m in ('clear_B','clear_A_redirect_B','blocked_A_redirect_B') else 'A')
            # In redirect runs, B must have been commanded, not just a B-labeled
            # empty report or an A-stop followed by an unexecuted plan.
            applied=(reports[m].get('redirect_command_applied') is True if 'redirect' in m else True)
            target_witness=(goal in targets[m] and reports[m].get('goal')==goal)
            if m=='clear_B':target_witness=bool(targets[m] and targets[m][0]=='B' and target_witness)
            mode_success[m]=bool(v.get('valid_clear') and applied and target_witness)
        aligned=bool(same_configuration and same_geometry and mode_success['clear_A'])
        if not aligned:
            category='UNMATCHED_OR_FAILED_CLEAR_A'
        elif not mode_success['clear_B']:
            category='B_DIRECT_CONTROL_FAILED'
        elif not mode_success['clear_A_redirect_B']:
            category='UNBLOCKED_REDIRECT_CONTROL_FAILED'
        elif not mode_success['blocked_A_redirect_B']:
            category='BLOCKER_OR_INTERACTION_SENSITIVITY'
        else:category='FIVE_WAY_B_STOP_SURROGATE_PASS'
        counts['complete_fiveway_controls']+=1
        counts['matched_fiveway_controls']+=int(aligned)
        for m in FOLDERS:counts[m+'_stop_proxy']+=int(mode_success[m])
        counts[category]+=1
        rows.append({'trial':trial,'status':category,'config_match_except_intervention':same_configuration,
                     'geometry_match':same_geometry,'surrogate_success_by_mode':mode_success,
                     'real_site_verified':False,'passenger_boarding_verified':False})
    return {'scope':'CARLA_CONTROLLED_VEHICLE_ONLY_NO_REAL_CURB_OR_BOARDING',
            'counts':dict(counts),'trials':rows,
            'interpretation':'B-direct control isolates target accessibility, no-blocker redirect isolates control timing; blocker influence can be tested only after both succeed.',
            'physical_handoff_verified':False}
