"""Strict structured failure-lesson request on the shared structured transport."""
import json
from .artifacts import canonical
from .structured_repository_provider import StructuredRepositoryModel

_STR_LIST = {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}  # minLength already accepted live; maxItems avoided
REFLECTION_FORMAT = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'failure_lesson', 'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'suspected_files': _STR_LIST,
                'suspected_functions': _STR_LIST,
                'root_cause': {'type': 'string', 'minLength': 1},
                'what_failed': {'type': 'string', 'minLength': 1},
                'next_steps': _STR_LIST,
            },
            'required': ['suspected_files', 'suspected_functions', 'root_cause', 'what_failed', 'next_steps'],
            'additionalProperties': False,
        },
    },
}
FIELDS = REFLECTION_FORMAT['json_schema']['schema']['required']


def decode_lesson(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate lesson field')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('Lesson must contain exactly the schema fields')
    for key in ('suspected_files', 'suspected_functions', 'next_steps'):
        if not isinstance(value[key], list) or any(not isinstance(x, str) or not x for x in value[key]):
            raise ValueError('Invalid list field')
    for key in ('root_cause', 'what_failed'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError('Invalid text field')
    return value


def lesson_input_bound(messages):
    """Same conservative byte rule as operations; reflection requests carry no assistant envelopes."""
    if any(m.get('role') == 'assistant' for m in messages):
        raise ValueError('Reflection requests are single-turn')
    return len(canonical(messages).encode()) + len(canonical(REFLECTION_FORMAT).encode()) + 2048 + 128 * len(messages)


class StructuredReflectionModel(StructuredRepositoryModel):
    RESPONSE_FORMAT = REFLECTION_FORMAT

    @staticmethod
    def decode_content(content):
        return decode_lesson(content)

    def input_bound(self, messages):
        return lesson_input_bound(messages)
