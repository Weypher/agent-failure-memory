"""Bounded scripted development loop. No API transport is connected."""
import json
from pathlib import Path
from repository_sandbox import execute
from repository_feedback import render

SYSTEM = '''Work in /work to implement the supplied repository task.
Reply with one JSON object: {"action":"shell","command":"..."},
{"action":"test"}, or {"action":"done"}. Shell commands can read and edit
repository files. The test action runs the supplied test command. Tests are
read-only. Network access is unavailable. You have at most eight responses.
Do not infer success from missing output or incomplete test execution.'''


def parse(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('Expected one JSON object')
    action = value.get('action')
    keys = {'action', 'command'} if action == 'shell' else {'action'}
    if action not in ('shell', 'test', 'done') or set(value) != keys:
        raise ValueError('Invalid action schema')
    if action == 'shell' and (not isinstance(value['command'], str) or not value['command'].strip()):
        raise ValueError('Expected nonempty shell command')
    return value


def save(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def run_scripted(task, spec, workspace, test_command, responses, output, arm):
    if set(task) != {'id', 'prompt'} or arm not in ('RAW', 'PROSE', 'JSON'):
        raise ValueError('Only inference-safe task fields and known arms accepted')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save(output/'task.json', task)
    messages = [{'role':'system','content':SYSTEM}, {'role':'user','content':task['prompt']}]
    status = 'response_limit'
    count = 0
    for number, response in enumerate(responses, 1):
        if number > 8:
            break
        count = number
        save(output/('%02d-request.json' % number), messages)
        save(output/('%02d-response.json' % number), {'text':response,'kind':'SCRIPTED_DEBUG'})
        messages.append({'role':'assistant','content':response})
        try:
            action = parse(response)
        except (ValueError, TypeError) as error:
            messages.append({'role':'user','content':'Action parsing failed: '+str(error)})
            continue
        if action['action'] == 'done':
            status = 'declared_done_not_scored'
            break
        argv = test_command if action['action'] == 'test' else ['/bin/sh','-c',action['command']]
        directory = output/('%02d-tool' % number)
        try:
            record = execute(spec, workspace, directory, argv)
            raw = (directory/'raw.log').read_bytes().decode('utf-8', errors='replace')
            if action['action'] == 'test':
                for key in ('events','lifecycle'):
                    try:
                        record[key] = json.loads((directory/(key+'.json')).read_text())
                    except (OSError, ValueError):
                        record[key] = None
                feedback = render(raw, record, arm)
            else:
                feedback = json.dumps(record)+'\nRAW OUTPUT:\n'+raw
        except Exception as error:
            save(output/('%02d-infrastructure-error.json' % number), {'type':type(error).__name__,'message':str(error)})
            status = 'infrastructure_error'
            break
        messages.append({'role':'user','content':feedback})
    else:
        status = 'script_exhausted'
    save(output/'transcript.json', messages)
    result = {'kind':'SCRIPTED_DEBUG','status':status,'responses':count,'paid_calls':0,
              'resolved':None,'note':'Completion claims are not scored outcomes.'}
    save(output/'terminal.json', result)
    return result
