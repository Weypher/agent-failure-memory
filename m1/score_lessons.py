"""Score DEV lessons after the stage (evaluation-only, no model). Uses pinned references.
Location accuracy (lenient and strict) plus simple event-consistency indicators: whether
the lesson text names a recorded failing test function or a recorded exception type.
Usage: python3 m1/score_lessons.py <manifest.json> <stage output_root> <out.json>
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accuracy  # noqa: E402


def main(manifest_path, output_root, target):
    m = Path(manifest_path); manifest = json.loads(m.read_text()); out = Path(output_root); rows = []
    for t in manifest['tasks']:
        case = out / t['task_id'].replace('/', '-'); row = {'task': t['task_id']}
        if not (case / 'lesson.json').exists():
            row['lesson'] = 'absent'; rows.append(row); continue
        lesson = json.loads((case / 'lesson.json').read_text()); sig = json.loads((case / 'signals.json').read_text())
        ref_dir = m.parent / 'reference' / t['task_id'].replace('/', '-')
        reference = accuracy.reference_locations(ref_dir / 'buggy', ref_dir / 'fixed', t['reference_changed'])
        text = ' '.join([lesson['root_cause'], lesson['what_failed']] + lesson['next_steps'])
        tests = {f.split(' (')[0].split('::')[-1].split('[')[0] for f in sig['failing']}
        errors = {m_.group(1) for line in sig['error_lines'] for m_ in [re.match(r'E\s+(\w+(?:Error|Exception|Warning))', line)] if m_}
        row.update(accuracy.score_lesson(lesson, reference),
                   mentions_failing_test=any(x in text for x in tests), mentions_recorded_error=any(x in text for x in errors),
                   attempt_modified_reference_file=bool(set(sig['modified_files']) & set(reference['files'])))
        rows.append(row)
    Path(target).write_text(json.dumps({'kind': 'DEV_LESSON_SCORES_NO_AGGREGATE_CLAIMS', 'rows': rows}, indent=1) + '\n')
    print(len(rows))


if __name__ == '__main__':
    main(*sys.argv[1:4])
