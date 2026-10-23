"""Structured-operation multi-turn execution core r3 (agent-failure-memory): identical to r2 except
that a settled incomplete/refused model output (ModelOutputIncomplete) ends the attempt with status
output_limit (model behaviour) instead of provider_or_budget_stop. Bound 96000; no truncation.

No CLI, credentials, model construction, or spending permission is provided.
"""
import hashlib
import json
import time
import traceback
import errno
from pathlib import Path
from repository_loop import parse, save
from repository_feedback import render
from repository_sandbox import execute
from harness.repository_action import input_upper_bound
from harness.test_event_score import score
from harness.agent import ModelOutputIncomplete



def diagnostic(error, phase, output):
    """Structured locations/codes, never arbitrary exception text or locals."""
    number=getattr(error, 'errno', None)
    return {'type': type(error).__name__, 'phase': phase,
            'errno': number if type(number) is int else None,
            'errno_name': errno.errorcode.get(number) if type(number) is int else None,
            'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno,
                        'function': frame.name}
                       for frame in traceback.extract_tb(error.__traceback__)],
            'cause_type': type(error.__cause__).__name__ if error.__cause__ else None,
            'attempt_artifacts': str(output),
            'message_policy': 'Arbitrary exception text omitted; inspect retained transport/tool artifacts.'}


def hashes(workspace, expected):
    return {name: hashlib.sha256((Path(workspace)/name).read_bytes()).hexdigest()
            for name in expected}


def run(*, model, messages, call_prefix, spec, workspace, test_command,
        expected_tests, test_hashes, arm, output, max_responses=8,
        input_limit=96000):
    if arm not in ('RAW', 'PROSE', 'JSON') or max_responses != 8 or input_limit != 96000:
        raise ValueError('Unexpected experimental configuration')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    messages = json.loads(json.dumps(messages))
    started = time.monotonic()
    timings = []
    status = 'response_limit'
    responses = 0
    assessment = None
    phase = 'initial_test_hashes'
    active_started = None
    try:
        if hashes(workspace, test_hashes) != test_hashes:
            raise ValueError('Test assets differ before execution')
        for number in range(1, max_responses+1):
            upper = input_upper_bound(messages)
            save(output/('%02d-request.json' % number),
                 {'messages': messages, 'input_upper_bound': upper})
            if upper > input_limit:
                status = 'context_limit'
                break
            before = time.monotonic()
            phase = 'model'
            active_started = before
            try:
                reply = model.complete(messages, call_prefix+'/'+str(number))
            except ModelOutputIncomplete as error:
                save(output/('%02d-output-incomplete.json' % number),
                     diagnostic(error, phase, output))
                timings.append({'step': number, 'kind': 'model', 'completed': False,
                                'seconds': time.monotonic()-before})
                active_started = None
                responses += 1  # a charged, retained response without a usable operation
                status = 'output_limit'
                break
            except Exception as error:
                save(output/('%02d-provider-error.json' % number),
                     diagnostic(error, phase, output))
                timings.append({'step': number, 'kind': 'model', 'completed': False,
                                'seconds': time.monotonic()-before})
                active_started = None
                status = 'provider_or_budget_stop'
                break
            timings.append({'step': number, 'kind': 'model',
                            'seconds': time.monotonic()-before})
            active_started = None
            responses += 1
            save(output/('%02d-response.json' % number),
                 {'text': reply.text, 'cost_nusd': reply.cost_nusd})
            messages.append({'role': 'assistant', 'content': reply.text})
            try:
                action = parse(reply.text)
            except (ValueError, TypeError):
                status = 'invalid_action_stop'
                break
            if action['action'] == 'done':
                status = 'declared_done'
                break
            directory = output/('%02d-tool' % number)
            argv = test_command if action['action'] == 'test' else ['/bin/sh', '-c', action['command']]
            before = time.monotonic()
            phase = action['action']
            active_started = before
            record = execute(spec, workspace, directory, argv)
            timings.append({'step': number, 'kind': action['action'],
                            'seconds': time.monotonic()-before})
            active_started = None
            raw = (directory/'raw.log').read_bytes().decode('utf-8', errors='replace')
            if record['timeout']:
                status = 'tool_timeout'
                break
            if action['action'] == 'test':
                for key in ('events', 'lifecycle'):
                    try:
                        record[key] = json.loads((directory/(key+'.json')).read_text())
                    except (OSError, ValueError):
                        record[key] = None
                intermediate = score(record, expected_tests, test_hashes,
                                     hashes(workspace, test_hashes))
                save(directory/'assessment.json', intermediate)
                if intermediate['status'] == 'invalid_execution':
                    status = 'invalid_test_execution'
                    break
                feedback = render(raw, record, arm)
            else:
                feedback = json.dumps(record)+'\nRAW OUTPUT:\n'+raw
            messages.append({'role': 'user', 'content': feedback})
        # Always preserve the candidate evaluation separately from attempt status.
        # A truncated/invalid attempt is never converted into a primary failure.
        before = time.monotonic()
        phase = 'final_evaluation'
        active_started = before
        record = execute(spec, workspace, output/'evaluation', test_command)
        for key in ('events', 'lifecycle'):
            try:
                record[key] = json.loads((output/'evaluation'/(key+'.json')).read_text())
            except (OSError, ValueError):
                record[key] = None
        assessment = score(record, expected_tests, test_hashes, hashes(workspace, test_hashes))
        timings.append({'kind': 'final_evaluation', 'seconds': time.monotonic()-before})
        active_started = None
        save(output/'evaluation.json', {'record': record, 'assessment': assessment})
    except Exception as error:
        if active_started is not None:
            timings.append({'kind': phase, 'completed': False,
                            'seconds': time.monotonic()-active_started})
        save(output/'infrastructure-error.json', diagnostic(error, phase, output))
        status = 'infrastructure_error'
    complete = status in ('declared_done', 'response_limit')
    valid = assessment is not None and assessment['status'] != 'invalid_execution'
    result = {'kind': model.kind, 'status': status, 'responses': responses,
              'candidate_assessment': assessment,
              'resolved': assessment['resolved'] if complete and valid else None,
              'elapsed_seconds': time.monotonic()-started, 'timings': timings,
              'note': 'SCRIPTED_DEBUG records are not model experiments.'}
    save(output/'transcript.json', messages)
    save(output/'terminal.json', result)
    return result
