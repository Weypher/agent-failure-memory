"""Resume manifest after a harness stop (prefix rule, main validity review r4 MV4-1).
Kept = the longest prefix of the previous manifest's dispatch order in which every task's row status
is 'complete'; everything after it is rerun in full, including tasks that overtook and finished
(their completion depended on run length, which depends on outcomes). Uses only run status/order. Inputs (prompts, statements, snapshots, specs)
are the previously frozen ones; pinned references are copied next to the new manifest.
Usage: python3 m1/make_resume_manifest.py <previous manifest> <previous outcomes.json> <out_dir> <workers>
"""
import json, shutil, sys
from pathlib import Path


def main(prev_manifest, prev_outcomes, out, workers):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    m = json.loads(Path(prev_manifest).read_text())
    status = {r['task']: r['status'] for r in json.loads(Path(prev_outcomes).read_text())}
    order = [t['task_id'] for t in m['tasks']]; prefix = 0
    while prefix < len(order) and status.get(order[prefix]) == 'complete':
        prefix += 1
    kept = order[:prefix]
    rerun = m['tasks'][prefix:]
    overtakers = [t for t in order[prefix:] if status.get(t) == 'complete']
    shutil.copytree(Path(prev_manifest).parent / 'reference', out / 'reference')
    m.update(tasks=rerun, workers=int(workers), status='MAIN_RESUME_INPUTS_FROZEN_FOR_REVIEW',
             resume={'previous_manifest': str(Path(prev_manifest).resolve()), 'previous_outcomes': str(Path(prev_outcomes).resolve()),
                     'kept_complete_tasks': kept, 'rerun_tasks': [t['task_id'] for t in rerun],
                     'discarded_complete_overtakers': overtakers,
                     'rule': 'longest all-complete prefix of dispatch order kept; all later tasks rerun in full (their previous data discarded and disclosed)'})
    (out / 'manifest.json').write_text(json.dumps(m, indent=1) + '\n')
    (out / 'kept-tasks.json').write_text(json.dumps(kept, indent=1) + '\n')
    print(json.dumps({'kept': len(kept), 'rerun': len(rerun), 'overtakers': overtakers}))


if __name__ == '__main__':
    main(*sys.argv[1:5])
