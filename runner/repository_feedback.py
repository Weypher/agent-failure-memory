"""Information-matched feedback views, independent of reference tests and fixes."""
import json

COMMON = ('Investigate both environment and source causes. Test execution stages '
          'are observations, not root-cause labels. Missing records mean unknown. '
          'Use the raw output when interpreting observations.')


def observations(record):
    """Project only observable process/recorder fields; never consume scorer output."""
    lifecycle = record.get('lifecycle')
    sidecar = record.get('events')
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    if not isinstance(sidecar, dict):
        sidecar = {}
    return {
        'process_exit': record.get('exit'),
        'timeout': record.get('timeout'),
        'lifecycle_state': lifecycle.get('state'),
        'lifecycle_pytest_exit': lifecycle.get('pytest_exitstatus'),
        'lifecycle_exception_type': lifecycle.get('exception_type'),
        'recorder_present': isinstance(record.get('events'), dict),
        'recorder_pytest_exit': sidecar.get('exitstatus'),
        'collected_tests': sidecar.get('testscollected'),
        'events': sidecar.get('events'),
    }


LABELS = {
    'process_exit': 'The process exit code is',
    'timeout': 'The timeout indicator is',
    'lifecycle_state': 'The recorded outer lifecycle state is',
    'lifecycle_pytest_exit': 'The outer lifecycle pytest exit code is',
    'lifecycle_exception_type': 'The outer lifecycle exception type is',
    'recorder_present': 'A recorder object is present',
    'recorder_pytest_exit': 'The recorder pytest exit code is',
    'collected_tests': 'The recorded collected-test count is',
    'events': 'The ordered recorded events are',
}


def render(raw, record, arm):
    if arm not in ('RAW', 'PROSE', 'JSON'):
        raise ValueError('Unknown arm')
    result = COMMON + '\n\nRAW OUTPUT:\n' + raw
    if arm == 'RAW':
        return result
    values = observations(record)
    if arm == 'JSON':
        addition = json.dumps(values, ensure_ascii=False, sort_keys=True)
    else:
        # Every field appears exactly once; JSON values preserve nulls, types,
        # node identifiers and ordered events without an interpretive gloss.
        lines = [LABELS[k] + ' ' + json.dumps(v, ensure_ascii=False) + '.'
                 for k, v in values.items() if k != 'events']
        events = values['events']
        if isinstance(events, list):
            lines.append('The number of ordered recorded events is ' + str(len(events)) + '.')
            event_labels = {'event': 'event type', 'nodeid': 'node identifier',
                            'phase': 'test phase', 'outcome': 'outcome',
                            'exception_type': 'exception type'}
            for index, event in enumerate(events):
                if isinstance(event, dict):
                    clauses = [event_labels.get(k, 'field ' + json.dumps(k)) + ' is ' +
                               json.dumps(v, ensure_ascii=False) for k, v in event.items()]
                    lines.append('Event ' + str(index + 1) + ': ' + '; '.join(clauses) + '.')
                else:
                    lines.append('Event ' + str(index + 1) + ' has malformed value ' +
                                 json.dumps(event, ensure_ascii=False) + '.')
        else:
            lines.append('The ordered recorded events value is ' + json.dumps(events) + '.')
        addition = '\n'.join(lines)
    return result + '\n\nEXECUTION OBSERVATIONS (null means unknown):\n' + addition
