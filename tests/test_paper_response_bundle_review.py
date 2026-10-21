"""Exact paired account/activity response review; private and offline."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from nexus import paper_response_bundle_review as subject
from nexus.paper_read_pacing import PaperReadPacing


def snapshot(root):
    return sorted((str(path.relative_to(root)),path.read_bytes())
        for path in Path(root).rglob('*') if path.is_file())


def command(options):
    args=[]
    mapping=(('pacing','pacing_path'),('activity-store','activity_store_path'),
        ('expected-account','expected_account_id'),
        ('expected-pacing-head','expected_pacing_head'),
        ('expected-activity-head','expected_activity_head'),
        ('account-session-head','account_session_head'),
        ('account-before','account_before_path'),('positions','positions_path'),
        ('orders','orders_path'),('account-after','account_after_path'),
        ('account-observation','account_observation_path'),
        ('activity-session-head','activity_session_head'),
        ('activity-account-before','activity_account_before_path'),
        ('activity-account-after','activity_account_after_path'))
    for flag,key in mapping:args.extend(('--'+flag,str(options[key])))
    for path in options['activity_page_paths']:args.extend(('--activity-page',str(path)))
    return args


@pytest.mark.parametrize('two_pages',[False,True])
def test_exact_adjacent_response_bundle_replays_without_writes(tmp_path,two_pages):
    options=subject._fixture(tmp_path,two_pages=two_pages);before=snapshot(tmp_path)
    result=subject.review(**options)
    assert result['mode']==subject.MODE and result['status']=='REVIEW_REQUIRED'
    assert result['response_bodies']==7+int(two_pages) and result['activity_pages']==1+int(two_pages)
    assert result['exact_response_bytes_match'] and result['account_normalization_replayed']
    assert result['account_observation_replayed'] and result['activity_normalization_replayed']
    assert result['activity_observation_replayed'] and result['activity_receipt_replayed']
    assert result['sessions_adjacent'] and result['activity_session_is_current_tail']
    assert result['bounded_adjacent_session_start_gap']
    assert result['adjacent_session_start_gap_seconds']==60
    assert result['adjacent_session_start_gap_limit_seconds']==300
    assert result['component_session_timing_envelopes_match']
    assert result['adjacent_account_fields_match']
    assert result['overlapping_order_identities_match']
    assert result['overlapping_order_identities']==1
    assert result['overlapping_order_progress_monotonic']
    assert result['overlapping_order_progressions']==1
    assert len(result['overlapping_order_progress_sha256'])==64
    assert len(result['reported_open_order_identity_sha256'])==64
    assert len(result['activity_order_identity_sha256'])==64
    assert len(result['adjacent_account_fields_sha256'])==64
    assert result['journals_rechecked_unchanged'] and result['admission_reserved'] is False
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert result==subject.review(**options) and snapshot(tmp_path)==before


def test_changed_component_bytes_stale_heads_and_cross_component_alias_fail_closed(tmp_path):
    options=subject._fixture(tmp_path);page=options['activity_page_paths'][0]
    page.write_bytes(page.read_bytes()+b' ')
    with pytest.raises(ValueError,match='RESPONSE_DIGEST_DISAGREEMENT'):
        subject.review(**options)
    options=subject._fixture(tmp_path/'second')
    with pytest.raises(ValueError,match='PACING_HEAD_CHANGED'):
        subject.review(**dict(options,expected_pacing_head='f'*64))
    with pytest.raises(ValueError,match='ACTIVITY_HEAD_CHANGED'):
        subject.review(**dict(options,expected_activity_head='f'*64))
    with pytest.raises(ValueError,match='DISTINCT_PATHS_REQUIRED'):
        subject.review(**dict(options,
            activity_account_before_path=options['account_before_path']))


def test_bundle_requires_immediate_current_tail_pair(tmp_path):
    options=subject._fixture(tmp_path)
    gate=PaperReadPacing(options['pacing_path'],options['expected_account_id'])
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1792454401,ownership=owner,operation='ACCOUNT_INVENTORY')
        gate.finish(token,admission=lambda:1792454402,status_code=401,ownership=owner,
            receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=['ACCOUNT'],
                request_sha256=['a'*64],response_sha256=[]))
    with pytest.raises(ValueError,match='SESSION_SEQUENCE_INVALID'):
        subject.review(**dict(options,expected_pacing_head=gate.inspect()['head_sha256']))


def test_bundle_rejects_adjacent_normalized_account_field_discontinuity(tmp_path):
    options=subject._fixture(tmp_path,activity_account_cash='999')
    with pytest.raises(ValueError,match='ADJACENT_ACCOUNT_FIELDS_CHANGED'):
        subject.review(**options)


def test_bundle_rejects_cross_response_order_identity_conflict(tmp_path):
    options=subject._fixture(tmp_path,account_order_client_id='different_client')
    with pytest.raises(ValueError,match='CROSS_RESPONSE_ORDER_IDENTITY_CHANGED'):
        subject.review(**options)


@pytest.mark.parametrize('account_changes,activity_changes,fill_changes,include_fill,code',[
    ({},{'updated_at':'2026-10-17T17:59:58+00:00'},{},True,'ORDER_UPDATE_TIME_REGRESSION'),
    ({'updated_at':'2026-10-17T18:00:01Z'},{},{},True,'ORDER_UPDATE_TIME_CONFLICT'),
    ({'filled_qty':'0.5','filled_avg_price':'99','status':'partially_filled'},
        {'filled_quantity':'0.4','average_fill_price':'100','status':'partially_filled'},
        {'qty':'0.4','cum_qty':'0.4','leaves_qty':'0.6'},True,'ORDER_FILL_REGRESSION'),
    ({'filled_qty':'0.5','filled_avg_price':'99','status':'partially_filled'},
        {'filled_quantity':'0.5','average_fill_price':'100','status':'partially_filled'},
        {'qty':'0.5','cum_qty':'0.5','leaves_qty':'0.5'},True,'ORDER_FILL_CORRECTION'),
    ({'status':'pending_cancel'},{'status':'new','filled_quantity':'0','average_fill_price':None},
        {},False,'ORDER_STATUS_REGRESSION'),
])
def test_bundle_rejects_cross_response_order_progress_regressions(
        tmp_path,account_changes,activity_changes,fill_changes,include_fill,code):
    options=subject._fixture(tmp_path,account_order_changes=account_changes,
        activity_order_changes=activity_changes,activity_fill_changes=fill_changes,
        activity_include_fill=include_fill)
    with pytest.raises(ValueError,match=code):subject.review(**options)


def test_bundle_rejects_adjacent_sessions_outside_bounded_start_gap(tmp_path):
    options=subject._fixture(tmp_path,activity_wall_clock=1792368241)
    with pytest.raises(ValueError,match='ADJACENT_SESSION_GAP_EXCEEDED'):
        subject.review(**options)


@pytest.mark.parametrize('component,times',[
    ('account',(1792367939,1792367939)),
    ('activity',(1792368001,1792368001)),
])
def test_bundle_rejects_observation_outside_paced_session_envelope(tmp_path,component,times):
    options=subject._fixture(tmp_path)
    subject._retime_fixture_pacing(options,**{component+'_times':times})
    with pytest.raises(ValueError,match='SESSION_TIMING_DISAGREEMENT'):
        subject.review(**options)


def test_timing_negative_control_closes_rewrite_connection(tmp_path,monkeypatch):
    options=subject._fixture(tmp_path);connections=[]
    original=subject.sqlite3.connect
    class TrackedConnection(subject.sqlite3.Connection):
        closed=False
        def close(self):
            self.closed=True
            return super().close()
    def tracked(*args,**kwargs):
        connection=original(*args,**dict(kwargs,factory=TrackedConnection))
        connections.append(connection)
        return connection
    monkeypatch.setattr(subject.sqlite3,'connect',tracked)
    subject._retime_fixture_pacing(options,account_times=(1792367939,1792367939))
    assert len(connections)==1 and connections[0].closed is True


def test_component_authority_promotion_is_refused(tmp_path,monkeypatch):
    options=subject._fixture(tmp_path);original=subject.activity_review.review
    def promoted(**kwargs):
        result=original(**kwargs);result['ready_for_routing']=True;return result
    monkeypatch.setattr(subject.activity_review,'review',promoted)
    with pytest.raises(ValueError,match='COMPONENT_DISAGREEMENT'):
        subject.review(**options)


def test_cli_is_network_vault_silent_and_failure_output_is_fixed(tmp_path):
    options=subject._fixture(tmp_path);before=snapshot(tmp_path)
    script='''
import sys
from nexus import paper_credentials
from nexus.paper_response_bundle_review import main
def denied():raise RuntimeError("VAULT_DENIED")
def audit(event,args):
 if event in ("socket.connect","socket.bind"):raise RuntimeError("NETWORK_DENIED")
paper_credentials._vault=denied;sys.addaudithook(audit)
raise SystemExit(main())
'''
    child=subprocess.run([sys.executable,'-c',script,*command(options)],cwd=Path(__file__).parents[1],
        env=dict(os.environ),capture_output=True,text=True,timeout=30)
    assert child.returncode==0 and not child.stderr
    result=json.loads(child.stdout);assert result['mode']==subject.MODE
    assert snapshot(tmp_path)==before
    options['positions_path'].write_text('[]',encoding='utf-8')
    child=subprocess.run([sys.executable,'-m','nexus.paper_response_bundle_review',*command(options)],
        cwd=Path(__file__).parents[1],env=dict(os.environ),capture_output=True,text=True,timeout=30)
    assert child.returncode==2 and child.stdout=='PAPER_RESPONSE_BUNDLE_REVIEW_FAILED\n'
    assert not child.stderr and str(tmp_path) not in child.stdout
