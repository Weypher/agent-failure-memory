"""Development-only pytest event recorder. Reports observations, never causes."""
import json,os
_events=[]
def pytest_collectreport(report):
 _events.append({'event':'collection','nodeid':report.nodeid,'outcome':report.outcome})
def pytest_runtest_logreport(report):
 _events.append({'event':'test','nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome})
def pytest_internalerror(excrepr,excinfo):
 _events.append({'event':'internal_error','exception_type':excinfo.typename})
def pytest_sessionfinish(session,exitstatus):
 payload={'exitstatus':int(exitstatus),'testscollected':session.testscollected,'events':_events,'root_cause':'not_inferred'}
 with open(os.environ['PHASE_PROBE_OUTPUT'],'w') as f:json.dump(payload,f,indent=2)
