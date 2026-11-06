"""Exact historical synthetic review must never exempt new findings."""
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from tools import review_secret_scan as policy


def finding(line=24):
    return dict(RuleID='generic-api-key', Commit=policy.FIXTURE_COMMIT, File=policy.FIXTURE_PATH,
        StartLine=line, EndLine=line, StartColumn=2, EndColumn=34 if line==24 else 40,
        Fingerprint=f'{policy.FIXTURE_COMMIT}:{policy.FIXTURE_PATH}:generic-api-key:{line}',
        Secret='REDACTED', Match='PRIVATE_MATCH', Author='PRIVATE_AUTHOR', Message='PRIVATE_MESSAGE')


def refingerprint(row):
    row['Fingerprint']=f"{row['Commit']}:{row['File']}:{row['RuleID']}:{row['StartLine']}"


def test_two_exact_findings_require_one_complete_blob_proof(monkeypatch):
    proofs=[]
    monkeypatch.setattr(policy,'_fixture_proof',lambda repository:proofs.append(repository))
    result=policy.review([finding(),finding(25)],scan_exit_code=1,repository='repo')
    assert proofs==['repo']
    assert result['passed'] and result['scan_complete']
    assert result['reviewed_scope_complete']
    assert result['raw_findings']==result['reviewed_synthetic_findings']==2
    assert result['blocking_findings']==0 and result['locations']==[]
    assert not result['history_rewritten'] and not result['scan_exclusions_added']
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('line',[24,25])
def test_one_sided_historical_result_is_incomplete(monkeypatch,line):
    proofs=[]
    monkeypatch.setattr(policy,'_fixture_proof',lambda repository:proofs.append(repository))
    result=policy.review([finding(line)],scan_exit_code=1,repository='repo')
    assert proofs==['repo']
    assert not result['passed'] and not result['scan_complete']
    assert not result['reviewed_scope_complete']
    assert result['raw_findings']==result['reviewed_synthetic_findings']==1
    assert result['blocking_findings']==0 and result['locations']==[]


def test_one_sided_historical_cli_uses_incomplete_exit(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:None)
    path=tmp_path/'scan.json';path.write_text(json.dumps([finding()]))
    assert policy.main(['--report',str(path),'--repository','PRIVATE_REPO',
        '--scan-exit-code','1'])==2
    output=capsys.readouterr();assert 'PRIVATE' not in output.out+output.err
    result=json.loads(output.out)
    assert not result['passed'] and not result['scan_complete']
    assert not result['reviewed_scope_complete']


def test_complete_reviewed_scope_plus_new_finding_is_blocking_not_incomplete(monkeypatch):
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:None)
    row=finding();row['Commit']='d'*40;refingerprint(row)
    result=policy.review([finding(),finding(25),row],scan_exit_code=1,repository='repo')
    assert result['scan_complete'] and result['reviewed_scope_complete']
    assert not result['passed'] and result['blocking_findings']==1


@pytest.mark.parametrize('line',[24,25])
def test_one_sided_review_plus_new_finding_remains_incomplete(monkeypatch,line):
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:None)
    row=finding();row['Commit']='e'*40;refingerprint(row)
    result=policy.review([finding(line),row],scan_exit_code=1,repository='repo')
    assert not result['passed'] and not result['scan_complete']
    assert not result['reviewed_scope_complete']
    assert result['reviewed_synthetic_findings']==1
    assert result['blocking_findings']==1


@pytest.mark.parametrize('change',['commit','path','rule','line','column'])
def test_same_fixture_text_in_any_new_scope_still_blocks(monkeypatch,change):
    row=finding()
    if change=='commit':row['Commit']='a'*40
    if change=='path':row['File']='tests/new_fixture.py'
    if change=='rule':row['RuleID']='another-rule'
    if change=='line':row['StartLine']=row['EndLine']=26
    if change=='column':row['EndColumn']=35
    refingerprint(row)
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:pytest.fail('unreviewed scope was exempted'))
    result=policy.review([row],scan_exit_code=1,repository='repo')
    assert not result['passed'] and result['blocking_findings']==1
    assert result['reviewed_synthetic_findings']==0
    assert result['locations'][0]['file_sha256']==hashlib.sha256(row['File'].encode()).hexdigest()
    assert 'PRIVATE' not in json.dumps(result)


def test_new_finding_cannot_hide_among_reviewed_findings(monkeypatch):
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:None)
    row=finding();row['Commit']='b'*40;refingerprint(row)
    result=policy.review([finding(),row,finding(25)],scan_exit_code=1,repository='repo')
    assert not result['passed'] and result['raw_findings']==3
    assert result['reviewed_synthetic_findings']==2 and result['blocking_findings']==1


@pytest.mark.parametrize(('rows','code'),[([],1),([finding()],0),([],2),([],127),([],True),({},0)])
def test_failed_or_inconsistent_scanner_cannot_be_green(rows,code):
    with pytest.raises(policy.SecretReviewError):policy.review(rows,scan_exit_code=code,repository='repo')


@pytest.mark.parametrize('change',['duplicate','fingerprint','unredacted','bool_line','missing','reversed'])
def test_malformed_or_duplicate_reports_are_incomplete(monkeypatch,change):
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:None)
    row=finding();rows=[row]
    if change=='duplicate':rows.append(dict(row))
    if change=='fingerprint':row['Fingerprint']='invalid'
    if change=='unredacted':row['Secret']='PRIVATE_VALUE'
    if change=='bool_line':row['StartLine']=True
    if change=='missing':row.pop('EndColumn')
    if change=='reversed':row['EndColumn']=1
    with pytest.raises(policy.SecretReviewError):policy.review(rows,scan_exit_code=1,repository='repo')


def test_clean_scan_does_not_need_historical_source(monkeypatch):
    monkeypatch.setattr(policy,'_fixture_proof',lambda _:pytest.fail('unneeded Git access'))
    result=policy.review([],scan_exit_code=0,repository='missing')
    assert result['passed'] and result['scan_complete'] and result['reviewed_scope_complete']


def test_blob_proof_requires_exact_bytes_bounded_size_and_fixed_git_reference(monkeypatch):
    raw=b'reviewed synthetic source';calls=[]
    monkeypatch.setattr(policy,'FIXTURE_SHA256',hashlib.sha256(raw).hexdigest())
    def run(command,**kwargs):
        calls.append(command)
        assert kwargs==dict(capture_output=True,timeout=10,check=True)
        return SimpleNamespace(stdout=str(len(raw)).encode() if '-s' in command else raw)
    monkeypatch.setattr(policy.subprocess,'run',run)
    policy._fixture_proof('repo')
    assert all(command[-1]==policy.FIXTURE_COMMIT+':'+policy.FIXTURE_PATH for command in calls)
    monkeypatch.setattr(policy,'FIXTURE_SHA256','0'*64)
    with pytest.raises(policy.SecretReviewError):policy._fixture_proof('repo')
    monkeypatch.setattr(policy.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=b'1048577'))
    with pytest.raises(policy.SecretReviewError):policy._fixture_proof('repo')


def test_missing_blob_and_private_input_errors_are_sanitized(tmp_path,monkeypatch,capsys):
    path=tmp_path/'scan.json';path.write_text(json.dumps([finding()]))
    def missing(_):raise subprocess.CalledProcessError(1,['git','PRIVATE_PATH'],stderr=b'PRIVATE_ERROR')
    monkeypatch.setattr(policy,'_fixture_proof',missing)
    assert policy.main(['--report',str(path),'--repository','PRIVATE_REPO','--scan-exit-code','1'])==2
    output=capsys.readouterr();assert 'PRIVATE' not in output.out+output.err
    result=json.loads(output.out);assert not result['passed'] and not result['scan_complete']


def test_blocking_review_has_nonzero_exit_and_never_echoes_match(tmp_path,capsys):
    row=finding();row['Commit']='c'*40;refingerprint(row)
    path=tmp_path/'scan.json';path.write_text(json.dumps([row]))
    assert policy.main(['--report',str(path),'--repository','repo','--scan-exit-code','1'])==1
    output=capsys.readouterr();assert 'PRIVATE' not in output.out+output.err
    assert json.loads(output.out)['blocking_findings']==1


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','[NaN]'])
def test_duplicate_keys_and_nonfinite_json_are_refused(tmp_path,raw):
    path=tmp_path/'scan.json';path.write_text(raw)
    with pytest.raises(policy.SecretReviewError):policy.load_report(path)


def test_report_cap_and_nonregular_file_are_refused(tmp_path,monkeypatch):
    path=tmp_path/'scan.json';path.write_text('[]')
    monkeypatch.setattr(policy,'MAX_REPORT_BYTES',1)
    with pytest.raises(policy.SecretReviewError):policy.load_report(path)
    with pytest.raises(policy.SecretReviewError):policy.load_report(tmp_path)


def test_workflow_keeps_full_scan_and_uses_separate_trusted_reviewer():
    from pathlib import Path
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/public-repo-security.yml').read_text()
    assert 'test -f policy/tools/review_secret_scan.py' in text
    assert 'python3 policy/tools/review_secret_scan.py --repository scan' in text
    assert '--scan-exit-code "$result"' in text
    assert "--log-opts='--all'" in text and '--redact=100' in text
    assert '--gitleaks-ignore-path /dev/null' in text and '--ignore-gitleaks-allow' in text
