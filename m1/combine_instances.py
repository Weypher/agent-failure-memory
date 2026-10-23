"""Concatenate instance tables from several routes (e.g. kept complete tasks of a stopped route
plus the resumed route). Refuses overlapping tasks; with --frame <manifest>, requires exact coverage of
the frame's tasks; --exclude <json list> drops listed tasks (predeclared sensitivity).
Usage: python3 m1/combine_instances.py [--frame M | --expected L] [--exclude E] <out.json> <in1.json> <in2.json> ...
"""
import json, sys
from pathlib import Path


def task_list(value):
    """A JSON list of task ids, or a late-kept.json dict (its 'late_kept_tasks'). Anything else is rejected."""
    if isinstance(value, dict):
        value = value.get('late_kept_tasks')
    if not isinstance(value, list) or any(not isinstance(t, str) or '/' not in t for t in value):
        raise SystemExit('Task list must be a list of task ids (or a dict with late_kept_tasks)')
    return value


def main(target, *tables, frame=None, exclude=(), expected=None):
    first, inst, seen, sources = [], [], set(), []
    exclude = set(task_list(list(exclude)) if not isinstance(exclude, (list, dict)) else task_list(exclude))
    for t in tables:
        d = json.loads(Path(t).read_text()); tasks = {a['task'] for a in d['first_attempts']}
        if tasks & seen:
            raise SystemExit(f'Overlapping tasks across tables: {sorted(tasks & seen)[:5]}')
        seen |= tasks; sources.append({'path': t, 'tasks': len(tasks)})
        first += [a for a in d['first_attempts'] if a['task'] not in exclude]
        inst += [i for i in d['instances'] if i['task'] not in exclude]
    if exclude - seen:
        raise SystemExit(f'Excluded tasks not present in the tables: {sorted(exclude - seen)[:5]}')
    if frame is not None or expected is not None:
        want = {t['task_id'] for t in json.loads(Path(frame).read_text())['tasks']} if frame is not None else set(task_list(expected))
        if seen != want:
            raise SystemExit(f'Coverage mismatch: missing {sorted(want - seen)[:5]}, extra {sorted(seen - want)[:5]}')
    Path(target).write_text(json.dumps({'kind': 'MAIN_INSTANCE_TABLE_COMBINED', 'sources': sources, 'excluded_tasks': sorted(exclude),
                                        'first_attempts': first, 'instances': inst}, indent=1) + '\n')
    print(json.dumps(sources))


if __name__ == '__main__':
    args = sys.argv[1:]; kw = {}
    if '--frame' in args:
        i = args.index('--frame'); kw['frame'] = args[i + 1]; del args[i:i + 2]
    if '--exclude' in args:
        i = args.index('--exclude'); kw['exclude'] = json.loads(Path(args[i + 1]).read_text()); del args[i:i + 2]
    if '--expected' in args:  # explicit expected task list (second-stop analysis without full frame coverage)
        i = args.index('--expected'); kw['expected'] = json.loads(Path(args[i + 1]).read_text()); del args[i:i + 2]
    main(*args, **kw)
