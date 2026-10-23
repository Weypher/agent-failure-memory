"""Observe pytest.main lifecycle; never classify root causes or infer success."""
import json,os,sys

def persist(value):
    path=os.environ['PHASE_PROBE_LIFECYCLE']
    tmp=path+'.tmp'
    with open(tmp,'w') as f:
        json.dump(value,f,indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,path)

def main():
    persist({'state':'started'})
    try:
        import pytest
        code=pytest.main(sys.argv[1:])
    except BaseException as error:
        persist({'state':'raised','exception_type':type(error).__name__})
        raise
    else:
        persist({'state':'returned','pytest_exitstatus':int(code)})
        return int(code)

if __name__=='__main__':
    sys.exit(main())
