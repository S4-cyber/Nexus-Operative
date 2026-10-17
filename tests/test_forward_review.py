import copy
import json
import subprocess
import sys
import pytest
from nexus.forward_review import build, verify
from test_paper_forward import setup, warmup


def test_export_is_read_only_and_flags_injected_active_session(tmp_path, monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    r,g,c=setup(tmp_path,monkeypatch);warmup(r,c)
    monkeypatch.setattr(backend,'paper_forward_runner',r)
    sid=r.status()['session'];before=g.recovery_report()
    response=TestClient(backend.app).get(f'/api/paper/forward/sessions/{sid}/review.json')
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    assert sid in response.headers['content-disposition']
    bundle=response.json();blockers=bundle['payload']['review_blockers']
    assert 'STOP_SESSION_FOR_STABLE_REVIEW' in blockers
    assert 'PUBLIC_READER_PROVENANCE_NOT_ESTABLISHED' in blockers
    assert not verify(bundle)['release_approved']
    assert g.recovery_report()==before
    r.stop();stopped=build(r.evidence_report(sid))
    assert 'STOP_SESSION_FOR_STABLE_REVIEW' not in stopped['payload']['review_blockers']
    assert stopped['payload']['independent_review_required']


def test_summary_mutation_and_offline_verifier(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle();r.stop()
    bundle=build(r.evidence_report(r.status()['session']))
    path=tmp_path/'review.json';path.write_text(json.dumps(bundle),encoding='utf-8')
    result=subprocess.run([sys.executable,'-m','nexus.forward_review',str(path)],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['summary_digest_ok']
    changed=copy.deepcopy(bundle);changed['payload']['report']['counters']['BUY']=99
    with pytest.raises(ValueError,match='DIGEST_DISAGREEMENT'):verify(changed)
    path.write_text(json.dumps(changed),encoding='utf-8')
    assert subprocess.run([sys.executable,'-m','nexus.forward_review',str(path)],capture_output=True).returncode==1


@pytest.mark.parametrize('value',[None,[],{}, {'payload':[],'sha256':'bad'}])
def test_malformed_bundle_rejected(value):
    with pytest.raises(ValueError):verify(value)


def test_interrupted_public_summary_still_requires_review(tmp_path,monkeypatch):
    r,g,c=setup(tmp_path,monkeypatch);r.single_cycle()
    report=r.evidence_report(r.status()['session'])
    report['provisional_active_session']=False
    report['quote_evidence']['source_modes']=['PUBLIC_READER_SINGLE_SOURCE_UNATTESTED']
    payload=build(report)['payload']
    assert 'SESSION_INTERRUPTION_REQUIRES_REVIEW' in payload['review_blockers']
    assert payload['independent_review_required'] and not payload['release_approved']
