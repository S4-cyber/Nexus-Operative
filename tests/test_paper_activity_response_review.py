"""Private exact-byte activity normalization review; no external access."""
import json

import pytest

from nexus import paper_activity_response_review as subject
from nexus.broker_activity_store import BrokerActivityStore
from nexus.paper_read_pacing import PaperReadPacing


def test_exact_response_bytes_replay_activity_receipt_without_writes(tmp_path):
    options=subject._fixture(tmp_path)
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    result=subject.review(**options)
    assert result['mode']==subject.MODE and result['status']=='REVIEW_REQUIRED'
    assert result['response_bodies']==3 and result['activity_pages']==1
    assert result['rows_received']==result['normalized_activities']==1
    assert result['exact_response_bytes_match'] and result['account_fields_replayed']
    assert len(result['account_fields_sha256'])==64
    assert result['activity_normalization_replayed'] and result['collection_observation_replayed']
    assert result['retained_receipt_replayed'] and result['session_timing_envelope_match']
    assert result['journals_rechecked_unchanged']
    assert result['private_file_set_rechecked_unchanged']
    assert result['semantic_reader_failures_rechecked']
    assert result['admission_reserved'] is False
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert result==subject.review(**options)
    assert before=={path.name:path.read_bytes() for path in tmp_path.iterdir()}


def test_two_exact_activity_pages_replay_cursor_chain(tmp_path):
    result=subject.review(**subject._fixture(tmp_path,two_pages=True))
    assert result['response_bodies']==4 and result['activity_pages']==2
    assert result['rows_received']==result['normalized_activities']==101
    assert result['collection_observation_replayed'] and result['retained_receipt_replayed']


def test_changed_page_bytes_and_self_consistent_wrong_account_are_refused(tmp_path):
    options=subject._fixture(tmp_path)
    page=options['activity_page_paths'][0]
    page.write_bytes(page.read_bytes()+b' ')
    with pytest.raises(ValueError,match='RESPONSE_DIGEST_DISAGREEMENT'):
        subject.review(**options)
    options=subject._fixture(tmp_path/'second')
    path=options['account_before_path'];value=json.loads(path.read_text())
    value['cash']='999';path.write_text(json.dumps(value,separators=(',',':')),encoding='utf-8')
    with pytest.raises(ValueError,match='RESPONSE_DIGEST_DISAGREEMENT'):
        subject.review(**options)


def test_stale_heads_wrong_session_alias_and_wrong_operation_fail_closed(tmp_path):
    options=subject._fixture(tmp_path)
    with pytest.raises(ValueError,match='PACING_HEAD_CHANGED'):
        subject.review(**dict(options,expected_pacing_head='f'*64))
    with pytest.raises(ValueError,match='ACTIVITY_HEAD_CHANGED'):
        subject.review(**dict(options,expected_activity_head='f'*64))
    with pytest.raises(ValueError,match='SESSION_NOT_FOUND'):
        subject.review(**dict(options,session_head='f'*64))
    with pytest.raises(ValueError,match='DISTINCT_PATHS_REQUIRED'):
        subject.review(**dict(options,account_after_path=options['account_before_path']))
    gate=PaperReadPacing(options['pacing_path'],options['expected_account_id'])
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1792454401,ownership=owner,operation='ACCOUNT_INVENTORY')
        gate.finish(token,admission=lambda:1792454402,status_code=200,ownership=owner,
            receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=['ACCOUNT','POSITIONS','OPEN_ORDERS','ACCOUNT'],
                request_sha256=['a'*64]*4,response_sha256=['b'*64]*4))
    with pytest.raises(ValueError,match='SESSION_SCOPE_INVALID'):
        subject.review(**dict(options,expected_pacing_head=gate.inspect()['head_sha256'],
            session_head=gate.operation_history()[-1]['after_head_sha256']))


def test_cli_failure_is_fixed_and_does_not_echo_private_paths(tmp_path,capsys):
    options=subject._fixture(tmp_path)
    options['activity_page_paths'][0].write_text('[]',encoding='utf-8')
    import sys
    args=[]
    for flag,key in (('pacing','pacing_path'),('activity-store','activity_store_path'),
            ('expected-account','expected_account_id'),('expected-pacing-head','expected_pacing_head'),
            ('expected-activity-head','expected_activity_head'),('session-head','session_head'),
            ('account-before','account_before_path'),('account-after','account_after_path')):
        args.extend(('--'+flag,str(options[key])))
    for path in options['activity_page_paths']:args.extend(('--activity-page',str(path)))
    saved=sys.argv;sys.argv=['review',*args]
    try:assert subject.main()==2
    finally:sys.argv=saved
    output=capsys.readouterr()
    assert output.out.strip()=='PAPER_ACTIVITY_RESPONSE_REVIEW_FAILED' and output.err==''
    assert str(tmp_path) not in output.out


@pytest.mark.parametrize('index',range(6))
def test_every_private_input_is_rechecked_when_semantic_reader_fails(
        tmp_path,monkeypatch,index):
    options=subject._fixture(tmp_path,two_pages=True)
    paths=[options['pacing_path'],options['activity_store_path'],
        options['account_before_path'],*options['activity_page_paths'],
        options['account_after_path']]
    def mutate_then_fail(self):
        path=paths[index]
        path.write_bytes(path.read_bytes()+b' ')
        raise RuntimeError('injected lower-reader failure')
    monkeypatch.setattr(PaperReadPacing,'inspect',mutate_then_fail)
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_RESPONSE_REVIEW_FILE_SET_CHANGED'):
        subject.review(**options)


def test_late_normalizer_failure_cannot_hide_page_change(tmp_path,monkeypatch):
    options=subject._fixture(tmp_path)
    def mutate_then_fail(*args,**kwargs):
        path=options['activity_page_paths'][0]
        path.write_bytes(path.read_bytes()+b' ')
        raise RuntimeError('injected lower-reader failure')
    monkeypatch.setattr(subject,'_inputs',mutate_then_fail)
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_RESPONSE_REVIEW_FILE_SET_CHANGED'):
        subject.review(**options)


@pytest.mark.parametrize('target',['pacing','activity'])
def test_store_retarget_is_refused_at_outer_boundary(tmp_path,monkeypatch,target):
    options=subject._fixture(tmp_path)
    if target=='pacing':
        original=PaperReadPacing.inspect
        def retarget(self):
            result=original(self);self.path=options['account_before_path'];return result
        monkeypatch.setattr(PaperReadPacing,'inspect',retarget)
    else:
        original=BrokerActivityStore.inspect
        def retarget(self):
            result=original(self);self.path=options['account_before_path'];return result
        monkeypatch.setattr(BrokerActivityStore,'inspect',retarget)
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_RESPONSE_REVIEW_STORE_BINDING_CHANGED'):
        subject.review(**options)


def test_fixture_report_makes_file_set_guarantee_release_visible():
    result=subject.fixture_report()
    assert result['source']=='TEMPORARY_INJECTED_ACTIVITY_RESPONSE_REVIEW'
    assert result['passed'] and all(result[key] for key in subject.CHECKS)
    assert all(result[flag] is False for flag in subject.FLAGS)
