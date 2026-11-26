"""Disposable Git histories; no actual credentials or repository changes."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_audit',ROOT/'tools/public_repo_audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def git(root,*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout

def repository(tmp_path):
    git(tmp_path,'init');git(tmp_path,'config','user.name','Fixture');git(tmp_path,'config','user.email','fixture@example.invalid')
    return tmp_path


def commit(root,name,raw):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    git(root,'add','--',name);git(root,'commit','-m','fixture')
    return path


@pytest.mark.parametrize('name',['.env','.env.local','config.env','secrets/token.txt','storage/private.json','data/account.db','account.sqlite-wal','saved.db-journal','backup.zip','vault.key','saved.nexusenc','Nexus-Paper-Observation.json','nexus-backup-manifest.json'])
def test_sensitive_path_policy(name):assert audit.path_rule(name)


@pytest.mark.parametrize('name',['nexus/app.py','storage/README.txt','storage/.gitkeep','.env.example','validation/release_trials_0_110_0.json','SECURITY.md'])
def test_allowed_public_source_names(name):assert audit.path_rule(name) is None


def test_clean_full_history_and_no_content_echo(tmp_path):
    root=repository(tmp_path);commit(root,'source.py',b'print("public")\n')
    report=audit.audit(root,history=True)
    assert report['passed'] and report['history_scanned'] and report['blobs_checked']==1
    assert report['paths_checked']==1


@pytest.mark.parametrize('history',[False,True])
def test_tracked_symlink_is_rejected_in_current_and_history_modes(tmp_path,history):
    root=repository(tmp_path);target=root/'target.txt';target.write_text('public')
    link=root/'linked.txt'
    try:link.symlink_to('target.txt')
    except OSError:pytest.skip('file symlink unavailable')
    git(root,'add','--','target.txt','linked.txt');git(root,'commit','-m','symlink fixture')
    result=audit.audit(root,history=history)
    assert not result['passed']
    assert any(item['rule']=='NON_REGULAR_GIT_ENTRY' for item in result['findings'])


def test_removed_private_path_is_still_blocked(tmp_path):
    root=repository(tmp_path);path=commit(root,'.env.local',b'not-a-real-secret')
    path.unlink();git(root,'add','-u');git(root,'commit','-m','removed fixture')
    report=audit.audit(root,history=True)
    assert not report['passed'] and any(x['rule']=='ENVIRONMENT_FILE' for x in report['findings'])
    assert 'not-a-real-secret' not in json.dumps(report)


def test_private_blob_renamed_as_text_is_blocked(tmp_path):
    root=repository(tmp_path);commit(root,'ordinary.txt',b'SQLite format 3\x00' + b'fixture')
    report=audit.audit(root,history=True)
    assert not report['passed'] and report['findings'][0]['rule']=='PRIVATE_BINARY_OR_KEY_CONTENT'


def test_export_renamed_as_json_is_blocked_but_offline_fixture_report_is_allowed(tmp_path):
    root=repository(tmp_path);commit(root,'ordinary.json',json.dumps({'profile':'nexus.portable-paper-observation.v1','private':'fixture'}).encode())
    assert any(x['rule']=='PRIVATE_FINANCIAL_REPORT' for x in audit.audit(root,history=True)['findings'])
    commit(root,'validation/report.json',json.dumps({'mode':'BOUNDED_OFFLINE_RELEASE_TRIALS','fixture':True}).encode())
    assert not audit.audit(root,history=True)['passed']


@pytest.mark.parametrize(('raw','rule'),[
    (b'{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE","mode":"BOUNDED_OFFLINE_RELEASE_TRIALS","account_id":"private-fixture"}',
     'AMBIGUOUS_JSON_REQUIRES_REVIEW'),
    (b'{"mode":"BOUNDED_OFFLINE_RELEASE_TRIALS","mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE","account_id":"private-fixture"}',
     'AMBIGUOUS_JSON_REQUIRES_REVIEW'),
    (b'{"safe":{"value":1,"value":2}}','AMBIGUOUS_JSON_REQUIRES_REVIEW'),
    (b'{"value":NaN}','AMBIGUOUS_JSON_REQUIRES_REVIEW'),
    (b'{"value":Infinity}','AMBIGUOUS_JSON_REQUIRES_REVIEW'),
    (b'{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"',
     'MALFORMED_JSON_REQUIRES_REVIEW'),
    (b'\xef\xbb\xbf{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"',
     'MALFORMED_JSON_REQUIRES_REVIEW'),
    ('{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"'.encode('utf-16'),
     'MALFORMED_JSON_REQUIRES_REVIEW'),
])
def test_ambiguous_or_malformed_json_shaped_blob_requires_review(raw,rule):
    assert audit.json_rule(raw)==rule


@pytest.mark.parametrize('raw',[
    b'const value = {mode: "public"};',
    b'def public():\n    return {"mode":"fixture"}\n',
    b'not json {"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"',
    b'[build-system]\nrequires = ["setuptools"]\n',
])
def test_ordinary_non_json_source_is_not_misclassified(raw):
    assert audit.json_rule(raw) is None


def test_malformed_json_array_still_requires_review():
    assert audit.json_rule(b'[{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"') == (
        'MALFORMED_JSON_REQUIRES_REVIEW')


def test_repaired_historical_malformed_json_is_grandfathered_but_new_delta_is_not(tmp_path):
    root=repository(tmp_path)
    path=commit(root,'report.json',b'{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"')
    path.write_text('{"mode":"BOUNDED_OFFLINE_RELEASE_TRIALS"}\n')
    git(root,'add','--','report.json');git(root,'commit','-m','repair')
    baseline=git(root,'rev-parse','HEAD').strip().decode()
    commit(root,'source.py',b'print("public")\n')
    candidate=git(root,'rev-parse','HEAD').strip().decode()
    assert audit.audit(root,history=True,baseline_commit=baseline,
        candidate_commit=candidate)['passed']

    path.write_bytes(b'[{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE"')
    git(root,'add','--','report.json');git(root,'commit','-m','new malformed')
    changed=git(root,'rev-parse','HEAD').strip().decode()
    report=audit.audit(root,history=True,baseline_commit=candidate,
        candidate_commit=changed)
    assert not report['passed']
    assert any(item['rule']=='MALFORMED_JSON_REQUIRES_REVIEW'
               for item in report['findings'])


def test_ambiguous_json_finding_never_echoes_private_content_or_path(tmp_path):
    root=repository(tmp_path);private='private-account-fixture'
    commit(root,'renamed-private-report.txt',(
        '{"mode":"PAPER_ACCOUNT_ACCEPTANCE_PROBE",'
        '"mode":"BOUNDED_OFFLINE_RELEASE_TRIALS","account_id":"'+private+'"}').encode())
    report=audit.audit(root,history=True);encoded=json.dumps(report)
    assert not report['passed']
    assert any(item['rule']=='AMBIGUOUS_JSON_REQUIRES_REVIEW'
               for item in report['findings'])
    assert private not in encoded and 'renamed-private-report.txt' not in encoded

@pytest.mark.parametrize('history',[False,True])
def test_bom_prefixed_private_json_is_blocked_when_renamed(tmp_path,history):
    root=repository(tmp_path)
    private='private-account-fixture'
    raw=b'\xef\xbb\xbf'+json.dumps({
        'mode':'PAPER_ACCOUNT_ACCEPTANCE_PROBE',
        'account_sha256':private,
    }).encode()
    commit(root,'ordinary.txt',raw)
    result=audit.audit(root,history=history)
    assert not result['passed']
    assert any(item['rule']=='PRIVATE_FINANCIAL_REPORT' for item in result['findings'])
    assert private not in json.dumps(result)


def test_private_retired_order_id_baseline_is_blocked_even_when_renamed(tmp_path):
    root=repository(tmp_path)
    commit(root,'ordinary.json',json.dumps({
        'schema':'nexus.order-retired-id-baseline-review.v1',
        'retired_client_order_ids':['private-order-id']}).encode())
    result=audit.audit(root,history=True)
    assert not result['passed']
    assert any(item['rule']=='PRIVATE_ORDER_ID_BASELINE_REVIEW' for item in result['findings'])
    assert 'private-order-id' not in json.dumps(result)


def test_required_ref_must_be_fetched_and_shallow_clone_is_not_a_pass(tmp_path):
    root=repository(tmp_path);commit(root,'source.py',b'public')
    with pytest.raises(ValueError,match='GIT_AUDIT_INCOMPLETE'):audit.audit(root,history=True,required_refs=['refs/remotes/origin/missing'])
    head=git(root,'rev-parse','HEAD').strip();(root/'.git/shallow').write_bytes(head+b'\n')
    with pytest.raises(ValueError,match='FULL_HISTORY_REQUIRED'):audit.audit(root,history=True)


def test_renaming_same_blob_does_not_hide_a_private_historical_path(tmp_path):
    root=repository(tmp_path);path=commit(root,'safe.txt',b'public')
    path.rename(root/'.env');git(root,'add','-A');git(root,'commit','-m','fixture rename')
    assert not audit.audit(root,history=True)['passed']


def test_current_workflows_have_immutable_actions_and_no_privileged_secret_contexts():
    import re
    workflows=list((ROOT/'.github/workflows').glob('*.yml'))
    assert workflows
    for path in workflows:
        text=path.read_text()
        assert 'pull_request_target' not in text and 'secrets.' not in text
        assert 'contents: read' in text
        uses=re.findall(r'uses:\s*([^\s#]+)',text)
        assert uses and all(re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}',item) for item in uses)
        blocks=re.split(r'\n      - ',text)
        for block in blocks:
            if 'uses: actions/checkout@' in block:assert 'persist-credentials: false' in block


def test_pr_security_uses_trusted_policy_full_history_and_checksum_pinned_scanner():
    text=(ROOT/'.github/workflows/public-repo-security.yml').read_text()
    assert 'fetch-depth: 0' in text and 'github.event.pull_request.base.sha || github.sha' in text
    assert 'ref: ${{ github.event.pull_request.head.sha || github.sha }}' in text
    assert 'name: Bind scan to exact source commit' in text
    assert 'EXPECTED_SOURCE_COMMIT: ${{ github.event.pull_request.head.sha || github.sha }}' in text
    assert 'git -C scan rev-parse HEAD' in text
    assert 'python3 policy/tools/public_repo_audit.py --repository scan --history' in text
    assert 'BASELINE_COMMIT: ${{ github.event.pull_request.base.sha || github.sha }}' in text
    assert 'CANDIDATE_COMMIT: ${{ github.event.pull_request.head.sha || github.sha }}' in text
    assert '--baseline-commit "$BASELINE_COMMIT"' in text
    assert '--candidate-commit "$CANDIDATE_COMMIT"' in text
    assert 'sha256sum --check --strict' in text and "--log-opts='--all'" in text
    assert '--redact=100' in text and '--log-level fatal' in text
    assert '--ignore-gitleaks-allow' in text and '--gitleaks-ignore-path /dev/null' in text
    assert 'upload-artifact' not in text and "'Secret'" not in text and "'Match'" not in text


@pytest.mark.parametrize('mode',['RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW','PAPER_ACCOUNT_ACCEPTANCE_PROBE','PAPER_ACCEPTANCE_PACING_STATUS',
    'PAPER_ACTIVITY_ACCEPTANCE_PROBE','PAPER_ACTIVITY_ACCEPTANCE_STATUS','PAPER_ACTIVITY_ACCEPTANCE_HISTORY','PAPER_ACTIVITY_SSE_ACCEPTANCE_STATUS','PAPER_ACTIVITY_SSE_ACCEPTANCE_ATTEMPT',
    'PAPER_RECONCILIATION_ACCEPTANCE_REVIEW','PAPER_RECONCILIATION_STABILITY_GUARD_REVIEW','BROKER_REQUEST_ACTIVITY_REVIEW',
    'READ_ONLY_REQUEST_OBSERVATION_PREFIXES','PAPER_REQUEST_ACTIVITY_ACCEPTANCE_REVIEW','PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW','PAPER_ACCOUNT_EVIDENCE_STABILITY_GUARD_REVIEW','PAPER_PROVIDER_POST_RECONCILIATION_REVIEW','PAPER_PROVIDER_POST_FEE_REFERENCE_REVIEW','PAPER_PROVIDER_POST_KNOWN_FEE_RESERVE_REVIEW','SUPPLIED_ALPACA_ACTIVITY_SSE_LINK_REVIEW','SUPPLIED_ALPACA_ACTIVITY_SSE_RECONNECT_REVIEW','SUPPLIED_ALPACA_ACTIVITY_SSE_WIRE_SESSION_REVIEW','EXPLICIT_ALPACA_ACTIVITY_SSE_TRANSPORT','EXPLICIT_ALPACA_ACTIVITY_SSE_CHECKPOINT','EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_ATTEMPT','EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_RESULT_RECOVERY','EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_EMERGENCY_STOP_ABORT','EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_EMERGENCY_STOP_CLOSE','EXPLICIT_ALPACA_ACTIVITY_SSE_RECONNECT_OUTCOME_UNCERTAIN_CLOSE','PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_HISTORY','PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_CHECKPOINT','PRIVATE_ALPACA_ACTIVITY_SSE_CURSOR_BINDING','PRIVATE_ALPACA_ACTIVITY_SSE_RECONNECT_BUDGET','READ_ONLY_PAPER_ASSET_RULES',
    'PAPER_RECOVERY_SELECTION','PAPER_RECOVERY_STEP_REVIEW','EXPLICIT_SINGLE_PAPER_RECOVERY_STEP',
    'SUPPLIED_EXECUTION_QUALITY_REVIEW','PAPER_EXECUTION_QUALITY_REVIEW','READ_ONLY_PAPER_BTC_QUOTE_OBSERVATION','PAPER_REQUEST_QUOTE_HISTORY','PAPER_REQUEST_QUOTE_RECEIPT','PAPER_QUOTE_CAPTURE_STATUS','EXPLICIT_PAPER_QUOTE_CAPTURE','PRIVATE_PAPER_QUOTE_BUNDLE_REVIEW',
    'PAPER_PROVIDER_READ_BUDGET_REVIEWED_RECOVERY','PAPER_READ_PACING_REVIEWED_RECOVERY',
    'PAPER_ACCOUNT_RESPONSE_NORMALIZATION_REVIEW','PAPER_ACTIVITY_RESPONSE_NORMALIZATION_REVIEW',
    'PAPER_EXACT_RESPONSE_BUNDLE_REVIEW','READ_ONLY_BROKER_SUBMISSION_REVIEW'])
def test_standalone_operational_trial_report_is_private_even_when_renamed(tmp_path,mode):
    root=repository(tmp_path)
    commit(root,'ordinary.txt',json.dumps({'mode':mode,'fixture':True}).encode())
    result=audit.audit(root,history=True)
    assert not result['passed'] and any(f['rule']=='PRIVATE_FINANCIAL_REPORT' for f in result['findings'])


def test_real_broker_submission_review_is_private_in_history(tmp_path):
    from nexus.broker_request_journal import BrokerRequestJournal

    report=BrokerRequestJournal(
        tmp_path/'private.sqlite3',
        '01234567-89ab-cdef-0123-456789abcdef',
    ).submission_review('nx_private_order_001',evaluated_at=1000)
    root=tmp_path/'repo';root.mkdir()
    repository(root);commit(root,'ordinary.json',json.dumps(report).encode())
    result=audit.audit(root,history=True)
    assert not result['passed']
    assert any(f['rule']=='PRIVATE_FINANCIAL_REPORT' for f in result['findings'])
    assert 'nx_private_order_001' not in json.dumps(result)


def test_quote_evidence_ciphertext_and_plaintext_profile_are_private_when_renamed(tmp_path):
    root=repository(tmp_path)
    commit(root,'cipher.txt',b'NEXUS-QUOTE-EVIDENCE-ENC-1\nPRIVATE_CIPHERTEXT_FIXTURE')
    commit(root,'ordinary.json',json.dumps({'profile':'nexus.private-paper-quote-evidence.v1'}).encode())
    result=audit.audit(root,history=True)
    assert not result['passed']
    assert {'PRIVATE_BINARY_OR_KEY_CONTENT','PRIVATE_FINANCIAL_REPORT'}<={f['rule'] for f in result['findings']}


def test_aggregate_history_byte_cap_still_refuses_without_skipping_objects(tmp_path,monkeypatch):
    root=repository(tmp_path);commit(root,'public.txt',b'public fixture')
    monkeypatch.setattr(audit,'MAX_BYTES',1)
    with pytest.raises(ValueError,match='HISTORY_BYTE_CAP'):audit.audit(root,history=True)


def test_per_object_cap_is_finite_and_fail_closed(tmp_path,monkeypatch):
    root=repository(tmp_path);commit(root,'public.txt',b'public fixture')
    monkeypatch.setattr(audit,'MAX_OBJECT_BYTES',1)
    with pytest.raises(ValueError,match='OBJECT_BYTE_CAP'):audit.audit(root,history=True)


def test_exact_base_to_candidate_delta_is_measured_after_complete_history_scan(tmp_path):
    root=repository(tmp_path);commit(root,'base.txt',b'base public fixture')
    baseline=git(root,'rev-parse','HEAD').strip().decode()
    commit(root,'candidate.txt',b'candidate public fixture')
    candidate=git(root,'rev-parse','HEAD').strip().decode()
    expected=len({line.split(b' ',1)[0] for line in
        git(root,'rev-list','--objects',candidate,'^'+baseline).splitlines()})
    report=audit.audit(root,history=True,baseline_commit=baseline,
        candidate_commit=candidate)
    assert report['passed'] and report['blobs_checked']==2
    assert report['baseline_commit']==baseline and report['candidate_commit']==candidate
    assert report['delta_objects_checked']==expected and report['delta_bytes_checked']>0


@pytest.mark.parametrize(('field','reason'),[
    ('baseline','INVALID_BASELINE_COMMIT'),
    ('candidate','INVALID_CANDIDATE_COMMIT'),
])
def test_delta_commits_require_exact_lowercase_object_ids(tmp_path,field,reason):
    root=repository(tmp_path);commit(root,'public.txt',b'public fixture')
    head=git(root,'rev-parse','HEAD').strip().decode()
    values=dict(baseline_commit=head,candidate_commit=head);values[field+'_commit']='HEAD'
    with pytest.raises(ValueError,match=reason):audit.audit(root,history=True,**values)


def test_missing_and_unrelated_delta_commits_fail_closed(tmp_path):
    root=repository(tmp_path);commit(root,'public.txt',b'public fixture')
    head=git(root,'rev-parse','HEAD').strip().decode()
    missing='0'*len(head)
    with pytest.raises(ValueError,match='BASELINE_COMMIT_UNAVAILABLE'):
        audit.audit(root,history=True,baseline_commit=missing,candidate_commit=head)
    tree=git(root,'write-tree').strip().decode()
    unrelated=subprocess.run(['git','-C',str(root),'commit-tree',tree],input=b'unrelated\n',
        check=True,capture_output=True).stdout.strip().decode()
    with pytest.raises(ValueError,match='BASELINE_NOT_ANCESTOR'):
        audit.audit(root,history=True,baseline_commit=head,candidate_commit=unrelated)


def test_delta_requires_both_commits_and_complete_history(tmp_path):
    root=repository(tmp_path);commit(root,'public.txt',b'public fixture')
    head=git(root,'rev-parse','HEAD').strip().decode()
    with pytest.raises(ValueError,match='DELTA_COMMIT_PAIR_REQUIRED'):
        audit.audit(root,history=True,baseline_commit=head)
    with pytest.raises(ValueError,match='DELTA_REQUIRES_HISTORY'):
        audit.audit(root,baseline_commit=head,candidate_commit=head)


@pytest.mark.parametrize(('limit','reason'),[
    ('MAX_DELTA_BYTES','DELTA_BYTE_CAP'),
    ('MAX_DELTA_OBJECTS','DELTA_OBJECT_CAP'),
])
def test_candidate_delta_caps_fail_closed(tmp_path,monkeypatch,limit,reason):
    root=repository(tmp_path);commit(root,'base.txt',b'base public fixture')
    baseline=git(root,'rev-parse','HEAD').strip().decode()
    commit(root,'candidate.txt',b'candidate public fixture')
    candidate=git(root,'rev-parse','HEAD').strip().decode()
    monkeypatch.setattr(audit,limit,0)
    with pytest.raises(ValueError,match=reason):
        audit.audit(root,history=True,baseline_commit=baseline,
            candidate_commit=candidate)


def test_history_budget_is_finite_and_above_measured_reachable_history():
    measured_bytes=1_652_385_179
    assert audit.MAX_BYTES == 4 * 1024 * 1024 * 1024
    assert audit.MAX_OBJECT_BYTES == 16 * 1024 * 1024
    assert audit.MAX_DELTA_BYTES == 512 * 1024 * 1024
    assert audit.MAX_DELTA_OBJECTS == 4096
    assert measured_bytes < audit.MAX_BYTES <= 4 * 1024 * 1024 * 1024


@pytest.mark.parametrize('reason,expected',[('HISTORY_BYTE_CAP','HISTORY_BYTE_CAP'),
    ('PRIVATE_PATH_OR_SECRET','INTERNAL_ERROR')])
def test_incomplete_audit_reports_only_allowlisted_reason(tmp_path,monkeypatch,capsys,reason,expected):
    import sys
    monkeypatch.setattr(sys,'argv',['audit','--repository',str(tmp_path)])
    def failed(*args,**kwargs):raise ValueError(reason)
    monkeypatch.setattr(audit,'audit',failed)
    assert audit.main()==2
    assert capsys.readouterr().out=='PUBLIC_REPOSITORY_AUDIT_INCOMPLETE:'+expected+'\n'
