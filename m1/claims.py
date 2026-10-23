"""Rule-based claim checks for lessons (validity V1/R2-V3). Evaluation-only.

A lesson's free text is checked against the failure record the model actually read:
test-like identifiers (test_*) must name a selected test; exception-like identifiers
(*Error/*Exception/*Warning) must appear in the record. Counts unsupported claims;
it does not judge causal correctness (reported as a limitation).
"""
import re

TEST = re.compile(r'\b(test_\w+)')
EXC = re.compile(r'\b([A-Z]\w*(?:Error|Exception|Warning))\b')


def check(lesson, record_text, selected_tests):
    text = ' '.join([lesson['root_cause'], lesson['what_failed']] + lesson['next_steps'])
    names = {t.split('::')[-1].split('[')[0] for t in selected_tests}
    tests = set(TEST.findall(text)); excs = set(EXC.findall(text))
    unsupported_tests = sorted(t for t in tests if t not in names and t not in record_text)
    unsupported_excs = sorted(e for e in excs if e not in record_text)
    return {'test_claims': len(tests), 'unsupported_test_claims': unsupported_tests,
            'error_claims': len(excs), 'unsupported_error_claims': unsupported_excs,
            'any_unsupported': bool(unsupported_tests or unsupported_excs)}
