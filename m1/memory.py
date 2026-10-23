"""Failure-memory construction for retries. No model or ledger access here.

Main design (DEV version: git 0e2eb0d). One FAILURE RECORD is built from the failed
attempt: its transcript and final evaluation output, each message capped by a fixed
head+tail rule. HISTORY shows that record verbatim; REFLECT shows a model-written lesson
produced from exactly that record; SIGNAL is a different, deterministic digest (final-evaluation
facts + the workspace delta, which the record shows only indirectly); BOTH = REFLECT
+ SIGNAL. HISTORY vs REFLECT isolates model processing of identical information.
No arm ever sees reference-fix data.
"""
import json, re
from pathlib import Path

ARMS = ('NONE', 'HISTORY', 'REFLECT', 'SIGNAL', 'BOTH')
DEV_ARMS = ('NONE', 'REFLECT', 'SIGNAL', 'BOTH')
MESSAGE_CAP = 2000      # per transcript message in the failure record (head 1000 + tail 1000)
FINAL_LOG_CAP = 6000    # final evaluation output in the failure record (head 3000 + tail 3000)
CACHE_PARTS = ('.pytest_cache', '__pycache__', '.mypy_cache', '.cache')
MAX_ERROR_LINES = 30
MAX_ERROR_CHARS = 3000
MEMORY_HEADER = ('\n\nINFORMATION FROM A PREVIOUS ATTEMPT ON THIS TASK. That attempt did not make the '
                 'selected tests pass. The workspace has been reset to its original state.\n')

REFLECTION_SYSTEM = (
    'You are reviewing a failed attempt by a coding agent to resolve a repository issue. '
    'Write a lesson for a fresh attempt on the same task. Base every statement on the '
    'trajectory and the final test output provided; say what is uncertain. '
    'suspected_files and suspected_functions name the source locations you believe must change '
    '(paths relative to the repository root; function or Class.method names).')


def initial_messages(system, statement, initial_feedback):
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': statement.rstrip('\n') + '\n\nThe supplied test command has already been '
             'run on the initial workspace. Its output follows.\n' + initial_feedback}]


def cap(text, limit):
    """Fixed head+tail rule, identical for every task and arm (memory inputs only; the
    agent's live tool feedback is never truncated)."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f'\n[... {len(text) - 2 * half} characters omitted ...]\n' + text[-half:]


def transcript_text(transcript, limit=MESSAGE_CAP):
    """Render the failed attempt's conversation after the initial messages, each message capped."""
    parts = []
    for i, message in enumerate(transcript[2:], 1):
        label = 'AGENT OPERATION' if message['role'] == 'assistant' else 'RESULT'
        parts.append(f'--- {label} {i} ---\n{cap(message["content"], limit)}')
    return '\n'.join(parts)


def failure_record(transcript, final_raw):
    """The single information source for HISTORY and REFLECT."""
    return ('FAILED ATTEMPT:\n' + transcript_text(transcript)
            + '\n\nFINAL EVALUATION OUTPUT OF THE SELECTED TESTS:\n' + cap(final_raw, FINAL_LOG_CAP))


def reflection_messages(statement, transcript, final_raw):
    return [{'role': 'system', 'content': REFLECTION_SYSTEM},
            {'role': 'user', 'content': 'TASK:\n' + statement.rstrip('\n') + '\n\n' + failure_record(transcript, final_raw)}]


def source_changes(modified_files):
    """Workspace delta without tool caches (e.g. .pytest_cache from shell pytest runs)."""
    return sorted(f for f in modified_files if not any(p in f.split('/') for p in CACHE_PARTS) and not f.endswith('.pyc'))


def signals(evaluation, final_raw, modified_files):
    """Deterministic facts from the final evaluation and workspace delta."""
    assessment = evaluation.get('assessment') or {}
    failing = [f'{f["nodeid"]} ({f["phase"]})' for f in assessment.get('failures', [])]
    errors = [line for line in final_raw.splitlines() if line.startswith('E ')]
    shown, total = [], 0
    for line in errors[:MAX_ERROR_LINES]:
        if total + len(line) > MAX_ERROR_CHARS:
            break
        shown.append(line); total += len(line) + 1
    return {'status': assessment.get('status'), 'failing': failing, 'error_lines': shown,
            'error_lines_omitted': len(errors) - len(shown), 'modified_files': source_changes(modified_files)}


def render_signal(sig):
    lines = ['Observed results of the previous attempt:',
             f'Final evaluation status: {sig["status"]}.',
             'Failing selected tests: ' + (', '.join(sig['failing']) or 'none recorded') + '.',
             'Error lines from the final test output:']
    lines += ['  ' + l for l in sig['error_lines']] or ['  none']
    if sig['error_lines_omitted']:
        lines.append(f'  ({sig["error_lines_omitted"]} further error lines omitted)')
    lines.append('Files the previous attempt modified: ' + (', '.join(sig['modified_files']) or 'none') + '.')
    return '\n'.join(lines)


def render_lesson(lesson):
    return '\n'.join(['Notes written after the previous attempt:',
                      'Suspected files: ' + (', '.join(lesson['suspected_files']) or 'none') + '.',
                      'Suspected functions: ' + (', '.join(lesson['suspected_functions']) or 'none') + '.',
                      'Root cause: ' + lesson['root_cause'],
                      'What failed: ' + lesson['what_failed'],
                      'Next steps: ' + ('; '.join(lesson['next_steps']) or 'none') + '.'])


def retry_messages(base, arm, lesson=None, sig=None, record=None):
    """NONE is byte-identical to the first attempt's prompt."""
    if arm not in ARMS:
        raise ValueError('Unknown arm')
    messages = json.loads(json.dumps(base))
    if arm == 'NONE':
        return messages
    blocks = []
    if arm == 'HISTORY':
        if record is None:
            raise ValueError('Failure record required')
        blocks.append('Record of the previous attempt:\n' + record)
    if arm in ('REFLECT', 'BOTH'):
        if lesson is None:
            raise ValueError('Lesson required')
        blocks.append(render_lesson(lesson))
    if arm in ('SIGNAL', 'BOTH'):
        if sig is None:
            raise ValueError('Signals required')
        blocks.append(render_signal(sig))
    messages[1]['content'] += MEMORY_HEADER + '\n\n'.join(blocks) + '\n'
    return messages
