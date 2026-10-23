"""Predeclared timing sensitivity list (main validity review r4 MV4-2): kept tasks whose first request
was written within WINDOW seconds before the previous route's stop marker. Timing only, no outcomes.
Usage: python3 m1/late_kept.py <kept-tasks.json> <previous output_root> <out.json>
"""
import json, sys
from pathlib import Path

WINDOW = 3600


def main(kept_path, root, target):
    root = Path(root); stop = (root / 'stopped.json').stat().st_mtime
    starts = {}
    for t in json.loads(Path(kept_path).read_text()):
        req = root / 'tasks' / t.replace('/', '-') / 'A1-1' / 'attempt' / '01-request.json'
        starts[t] = req.stat().st_mtime
    late = sorted(t for t, s in starts.items() if stop - s < WINDOW)
    Path(target).write_text(json.dumps({'window_seconds': WINDOW, 'stop_mtime': stop,
                                        'seconds_before_stop': {t: round(stop - s) for t, s in sorted(starts.items())},
                                        'late_kept_tasks': late}, indent=1) + '\n')
    print(json.dumps({'late': late}))


if __name__ == '__main__':
    main(*sys.argv[1:4])
