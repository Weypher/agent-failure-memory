"""Scripted end-to-end M1 pipeline check. SCRIPTED_DEBUG: no model, credentials or ledger.

attempt-1 (scripted wrong edit -> test -> done) -> final evaluation -> SIGNAL extraction
-> REFLECT request construction (scripted lesson) -> four retry prompts -> scripted retries
-> lesson accuracy. Checks invariants a paid stage relies on.
Usage: python3 m1/debug_pipeline.py <sandbox-controls summary.json> <task> <out_dir>
"""
import filecmp, json, shutil, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'runner', ROOT / 'm1', ROOT / 'envs'):
    sys.path.insert(0, str(p))
from repository_snapshot import restore  # noqa: E402
from repository_sandbox import execute  # noqa: E402
from repository_feedback import render  # noqa: E402
from repository_multiturn_structured_r2 import run  # noqa: E402
from harness.repository_action import input_upper_bound  # noqa: E402
from harness.structured_reflection_provider import lesson_input_bound, decode_lesson  # noqa: E402
import memory, accuracy  # noqa: E402

from prompts import SYSTEM  # noqa: E402


class Script:
    kind = 'SCRIPTED_DEBUG'
    def __init__(self, replies): self.replies = list(replies)
    def complete(self, messages, call_id):
        return SimpleNamespace(text=self.replies.pop(0), cost_nusd=0)


def modified(initial_snapshot, work):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / 'base'; restore(initial_snapshot, base)
        files = {p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()} | \
                {p.relative_to(work).as_posix() for p in Path(work).rglob('*') if p.is_file()}
        return sorted(f for f in files if '__pycache__' not in f and (
            not (base / f).exists() or not (Path(work) / f).exists() or not filecmp.cmp(base / f, Path(work) / f, shallow=False)))


def main(summary, task_name, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    row = next(r for r in json.loads(Path(summary).read_text())['rows'] if r['task'] == task_name)
    base_dir = Path(summary).parent / task_name.replace('/', '-')
    task = json.loads((base_dir / 'task' / 'task.json').read_text()); spec = json.loads(Path(task['sandbox_spec']).read_text())
    statement = 'Resolve the following issue reported for this repository.\n\nIssue title: SCRIPTED DEBUG STATEMENT\n'
    # Initial test output, once per task, rendered exactly like ordinary RAW feedback.
    init = out / 'initial'; restore(task['initial_snapshot'], init / 'work')
    rec = execute(spec, init / 'work', init / 'output', task['test_command'], timeout=300)
    base = memory.initial_messages(SYSTEM, statement, render((init / 'output' / 'raw.log').read_text(errors='replace'), rec, 'RAW'))
    # Attempt 1: scripted wrong edit then test then done.
    a1 = out / 'attempt-1'; restore(task['initial_snapshot'], a1 / 'work')
    target = accuracy.reference_locations(task['fixed_checkout'].replace('/fixed', '/buggy'), task['fixed_checkout'], row['reference_changed'])['files'][0]
    script = Script([json.dumps({'action': 'shell', 'command': f'echo "# scripted edit" >> {target}'}),
                     '{"action":"test"}', '{"action":"done"}'])
    result = run(model=script, messages=base, call_prefix='SCRIPTED_DEBUG/a1', spec=spec, workspace=a1 / 'work',
                 test_command=task['test_command'], expected_tests=task['expected_tests'], test_hashes=task['test_hashes'],
                 arm='RAW', output=a1 / 'attempt')
    assert result['status'] == 'declared_done' and result['resolved'] is False, result
    evaluation = json.loads((a1 / 'attempt' / 'evaluation.json').read_text())
    final_raw = (a1 / 'attempt' / 'evaluation' / 'raw.log').read_text(errors='replace')
    transcript = json.loads((a1 / 'attempt' / 'transcript.json').read_text())
    sig = memory.signals(evaluation, final_raw, modified(task['initial_snapshot'], a1 / 'work'))
    assert sig['status'] == 'test_failures_observed' and sig['failing'] and sig['modified_files'] == [target], sig
    reflect_request = memory.reflection_messages(statement, transcript, final_raw)
    assert 'FINAL EVALUATION OUTPUT' in reflect_request[1]['content'] and final_raw in reflect_request[1]['content']
    lesson = decode_lesson(json.dumps({'suspected_files': [target], 'suspected_functions': ['scripted'], 'root_cause': 'scripted',
                                       'what_failed': 'scripted', 'next_steps': ['scripted']}))
    prompts = {arm: memory.retry_messages(base, arm, lesson, sig) for arm in memory.ARMS}
    assert prompts['NONE'] == base and all(prompts[a] != base for a in ('REFLECT', 'SIGNAL', 'BOTH'))
    assert memory.render_signal(sig) in prompts['BOTH'][1]['content'] and memory.render_lesson(lesson) in prompts['BOTH'][1]['content']
    retries = {}
    for arm, messages in prompts.items():
        d = out / f'retry-{arm}'; restore(task['initial_snapshot'], d / 'work')
        r = run(model=Script(['{"action":"done"}']), messages=messages, call_prefix=f'SCRIPTED_DEBUG/{arm}', spec=spec,
                workspace=d / 'work', test_command=task['test_command'], expected_tests=task['expected_tests'],
                test_hashes=task['test_hashes'], arm='RAW', output=d / 'attempt')
        assert r['status'] == 'declared_done' and r['resolved'] is False
        retries[arm] = {'input_upper_bound': input_upper_bound(messages), 'status': r['status']}
    reference = accuracy.reference_locations(task['fixed_checkout'].replace('/fixed', '/buggy'), task['fixed_checkout'], row['reference_changed'])
    record = {'kind': 'SCRIPTED_DEBUG_NOT_EXPERIMENT', 'task': task_name, 'paid_calls': 0, 'signals': sig,
              'reflection_input_bound': lesson_input_bound(reflect_request), 'retries': retries,
              'accuracy_of_scripted_lesson': accuracy.score_lesson(lesson, reference)}
    (out / 'debug.json').write_text(json.dumps(record, indent=1) + '\n')
    print(task_name, 'M1 scripted pipeline PASS', json.dumps(retries))


if __name__ == '__main__':
    main(*sys.argv[1:4])
