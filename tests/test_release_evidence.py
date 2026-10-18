"""Revalidate saved fixture results without replaying accounts or accessing IO."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from nexus import release_evidence as evidence


@pytest.fixture
def report():
    # Reuse existing synthetic result data, explicitly rebinding test metadata.
    # This demonstrates why matching hashes alone are NOT execution attestation.
    value=json.loads((evidence.ROOT/('validation/release_trials_'+evidence.REVISION.replace('.','_')+'.json')).read_text())
    value['version']=evidence.REVISION;value['source_fingerprint']=evidence.source_digest(evidence.ROOT)
    return value


def encoded(value):return json.dumps(value,allow_nan=False).encode()


def test_complete_result_revalidates_without_mutation_and_emits_only_scope_metadata(report):
    before=copy.deepcopy(report);summary=evidence.verify(encoded(report))
    assert report==before and summary['passed'] and summary['profiles_verified']==14
    assert summary['mode']=='VERIFIED_OFFLINE_EVIDENCE_ONLY'
    assert summary['source_fingerprint']==report['source_fingerprint']
    assert all(summary[k] is False for k in evidence.FLAGS)
    assert not {'profiles','environment','elapsed_seconds'}&summary.keys()


@pytest.mark.parametrize('flag',evidence.FLAGS)
def test_no_root_authority_claim_can_pass(report,flag):
    report[flag]=True
    with pytest.raises(ValueError,match='RELEASE_EVIDENCE_AUTHORITY'):evidence.verify(encoded(report))


@pytest.mark.parametrize('change,code',[
    ('version','VERSION'),('source','SOURCE_MISMATCH'),('missing','PROFILES'),('extra','PROFILES'),
    ('nested','PROFILE_FAILED'),('passed','RESULT'),('fingerprint_bool','FINGERPRINT'),('unknown','SHAPE'),
    ('timing_bool','TIMINGS'),('timing_huge','TIMINGS'),('metadata','METADATA')])
def test_stale_incomplete_or_falsely_successful_evidence_is_rejected(report,change,code):
    if change=='version':report['version']='0.1.0'
    if change=='source':report['source_fingerprint']['sha256']='0'*64
    if change=='missing':report['profiles'].pop('forward')
    if change=='extra':report['profiles']['private-extra']={}
    if change=='nested':report['profiles']['forward']['rounds']=19
    if change=='passed':report['passed']=1
    if change=='fingerprint_bool':report['source_fingerprint']['files']=True
    if change=='unknown':report['private-extra']='do not echo'
    if change=='timing_bool':report['elapsed_seconds']['forward']=True
    if change=='timing_huge':report['elapsed_seconds']['forward']=10**400
    if change=='metadata':report['environment']['private-extra']='do not echo'
    with pytest.raises(ValueError,match='RELEASE_EVIDENCE_'+code):evidence.verify(encoded(report))


@pytest.mark.parametrize('raw',[b'',b'{}',b'[]',b'{"passed":true,"passed":false}',b'{"x":NaN}',b'{"x":1e999}',b'\xff',b'['*2000+b']'*2000])
def test_strict_json_and_shape_fail_with_fixed_codes(raw):
    with pytest.raises(ValueError,match='^RELEASE_EVIDENCE_'):evidence.verify(raw)


def test_size_bound_precedes_json_parse(monkeypatch):
    monkeypatch.setattr(evidence,'MAX_BYTES',16)
    with pytest.raises(ValueError,match='RELEASE_EVIDENCE_SIZE'):evidence.verify(b'x'*17)


def test_source_change_during_validation_is_not_certified(report,monkeypatch):
    before=report['source_fingerprint'];calls=[]
    def digest(_):
        calls.append(True)
        return before if len(calls)==1 else dict(before,sha256='0'*64)
    monkeypatch.setattr(evidence,'source_digest',digest)
    with pytest.raises(ValueError,match='RELEASE_EVIDENCE_SOURCE_CHANGED'):evidence.verify(encoded(report))


def test_cli_valid_and_invalid_reports_have_private_fixed_output_and_no_data_writes(report,tmp_path):
    path=tmp_path/'report.json';path.write_bytes(encoded(report));before=path.read_bytes()
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path/'unused-account')}
    command=[sys.executable,'-m','nexus.release_evidence',str(path)]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['mode']=='VERIFIED_OFFLINE_EVIDENCE_ONLY'
    assert path.read_bytes()==before and not (tmp_path/'unused-account').exists()
    path.write_text('{"private":"do-not-echo"}')
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and result.stdout.strip()=='RELEASE_EVIDENCE_FAILED' and not result.stderr
    assert set(tmp_path.iterdir())=={path}


def test_report_file_must_be_bounded_regular_and_not_a_leaf_symlink(tmp_path,monkeypatch):
    path=tmp_path/'large';path.write_bytes(b'x'*17);monkeypatch.setattr(evidence,'MAX_BYTES',16)
    with pytest.raises(ValueError,match='RELEASE_EVIDENCE_FILE'):evidence._read(path)
    if os.name!='nt':
        link=tmp_path/'link';link.symlink_to(path)
        with pytest.raises(ValueError,match='RELEASE_EVIDENCE_FILE'):evidence._read(link)
        fifo=tmp_path/'fifo';os.mkfifo(fifo)
        with pytest.raises(ValueError,match='RELEASE_EVIDENCE_FILE'):evidence._read(fifo)
