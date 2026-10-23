"""One strict repository operation per response; not a task-success scorer."""
import json
from .artifacts import canonical

RESPONSE_FORMAT = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'repository_operation', 'strict': True,
        'schema': {
            'type': 'object',
            'properties': {'operation': {'anyOf': [
                {'type':'object', 'properties':{'action':{'type':'string','enum':['shell']},
                 'command':{'type':'string','minLength':1}},
                 'required':['action','command'], 'additionalProperties':False},
                {'type':'object', 'properties':{'action':{'type':'string','enum':['test','done']}},
                 'required':['action'], 'additionalProperties':False}
            ]}},
            'required':['operation'], 'additionalProperties':False
        }
    }
}

def wire_messages(messages):
    result=[]
    for message in messages:
        row=dict(message)
        if row.get('role')=='assistant':
            # The runner stores normalized executed actions. Restore the exact
            # operation envelope before sending conversation history.
            operation=json.loads(row['content'])
            row['content']=canonical({'operation':operation})
            decode(row['content'])
        result.append(row)
    return result

def input_upper_bound(messages):
    return len(canonical(wire_messages(messages)).encode())+len(canonical(RESPONSE_FORMAT).encode())+2048+128*len(messages)

def decode(text):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate operation field')
            result[key]=value
        return result
    value=json.loads(text,object_pairs_hook=unique)
    if not isinstance(value,dict) or set(value)!={'operation'}:
        raise ValueError('Exactly one operation envelope required')
    operation=value['operation']
    if not isinstance(operation,dict):raise ValueError('Operation must be an object')
    action=operation.get('action')
    if action=='shell':
        if set(operation)!={'action','command'} or not isinstance(operation['command'],str) or not operation['command'].strip():
            raise ValueError('Shell operation requires a nonempty command')
    elif action in ('test','done'):
        if set(operation)!={'action'}:raise ValueError('Unexpected operation fields')
    else:raise ValueError('Unknown operation')
    return operation
