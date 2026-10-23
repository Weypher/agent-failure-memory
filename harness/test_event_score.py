"""Conservative event scorer. Caller must obtain hashes outside editable agent state."""
def score(record, expected_tests, expected_hashes, observed_hashes):
    def invalid(reason):
        return {'status':'invalid_execution','resolved':False,'reason':reason}
    if not isinstance(record,dict):return invalid('malformed_record')
    if not isinstance(expected_tests,(list,tuple)) or any(not isinstance(x,str) or not x for x in expected_tests):return invalid('invalid_test_manifest')
    def valid_hashes(value):
        return isinstance(value,dict) and bool(value) and all(isinstance(k,str) and bool(k) and isinstance(v,str) and len(v)==64 and all(c in '0123456789abcdef' for c in v) for k,v in value.items())
    if not valid_hashes(expected_hashes) or not valid_hashes(observed_hashes):return invalid('invalid_hash_maps')
    if type(record.get('timeout')) is not bool:return invalid('unknown_timeout_status')
    expected=set(expected_tests)
    if not expected or len(expected)!=len(expected_tests):return invalid('invalid_test_manifest')
    if not expected_hashes or observed_hashes!=expected_hashes:return invalid('test_integrity_unverified')
    if record.get('timeout'):return invalid('timeout')
    lifecycle=record.get('lifecycle')
    events=record.get('events')
    if not isinstance(lifecycle,dict) or lifecycle.get('state')!='returned':return invalid('pytest_did_not_return')
    if not isinstance(events,dict) or not isinstance(events.get('events'),list):return invalid('missing_events')
    code=record.get('exit')
    if type(code) is not int or code not in (0,1):return invalid('non_test_exit')
    if type(events.get('exitstatus')) is not int or type(lifecycle.get('pytest_exitstatus')) is not int:return invalid('invalid_exit_type')
    if events.get('exitstatus')!=code or lifecycle.get('pytest_exitstatus')!=code:return invalid('exit_mismatch')
    if type(events.get('testscollected')) is not int:return invalid('invalid_collection_count_type')
    if events.get('testscollected')!=len(expected):return invalid('collection_count_mismatch')
    phases={}
    collections=set()
    for item in events['events']:
        if not isinstance(item,dict):return invalid('malformed_event')
        if item.get('event')=='collection':
            node=item.get('nodeid')
            if not isinstance(node,str):return invalid('invalid_collection_node')
            if node in collections:return invalid('duplicate_collection_event')
            collections.add(node)
            if item.get('outcome')!='passed':return invalid('collection_not_passed')
        elif item.get('event')=='test':
            node,phase,outcome=item.get('nodeid'),item.get('phase'),item.get('outcome')
            if not isinstance(node,str) or not isinstance(phase,str):return invalid('malformed_test_event')
            if node not in expected or phase not in ('setup','call','teardown'):return invalid('unexpected_test_event')
            if outcome not in ('passed','failed'):return invalid('nonterminal_or_skipped_test')
            if (node,phase) in phases:return invalid('duplicate_test_phase')
            phases[node,phase]=outcome
        else:return invalid('unexpected_or_internal_event')
    required={(node,phase) for node in expected for phase in ('setup','call','teardown')}
    if set(phases)!=required:return invalid('incomplete_test_phases')
    failures=[{'nodeid':n,'phase':p} for (n,p),v in sorted(phases.items()) if v=='failed']
    if (code==0)!= (not failures):return invalid('exit_event_contradiction')
    return {'status':'resolved' if not failures else 'test_failures_observed','resolved':not failures,'failures':failures,'root_cause':'not_inferred'}
