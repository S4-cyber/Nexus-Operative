"""Fixed endpoint consistency in real consumers and self-consistent copies."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import subprocess
import sys

import pytest

from nexus import paper_read_pacing as pacing
from nexus import paper_quote_bundle as bundle
from nexus import paper_quote_capture as capture
from nexus import paper_portal_probe as portal
from nexus.alpaca_paper_contract import canonical, sha
from test_paper_credentials import vault, save
from test_alpaca_paper_inspection import ACCOUNT, Opener, account
from test_paper_acceptance_probe import run as account_read
from test_paper_asset_rules import asset
from test_paper_quote_capture import args as quote_args, status_options
from test_paper_quote_bundle import files, PHRASE


def wrong_finish(monkeypatch, index):
    original = pacing.PaperReadPacing.finish
    def changed(self, token, **kwargs):
        receipt = deepcopy(kwargs['receipt'])
        receipt['request_sha256'][index] = sha(dict(method='GET', target='wrong_fixture_target'))
        return original(self, token, **dict(kwargs, receipt=receipt))
    monkeypatch.setattr(pacing.PaperReadPacing, 'finish', changed)


@pytest.mark.parametrize('index', range(4))
def test_inventory_consumer_suppresses_wrong_endpoint_receipt(vault,tmp_path,monkeypatch,index):
    save();wrong_finish(monkeypatch,index)
    path=tmp_path/'pacing.sqlite3';opener=Opener()
    with pytest.raises(ValueError,match='PAPER_ACCEPTANCE_PROBE_FAILED'):account_read(path,opener)
    gate=pacing.PaperReadPacing(path,ACCOUNT);before=path.read_bytes()
    assert len(opener.requests)==4 and gate.inspect()['last_outcome']=='SUCCESS'
    assert gate.inspect()['pending_token'] is None
    assert gate.operation_history()[0]['request_targets_bound'] is True
    assert not pacing.fixed_targets_match(gate.operation_history()[0],'ACCOUNT_INVENTORY')
    assert path.read_bytes()==before


@pytest.mark.parametrize('index', range(3))
def test_asset_consumer_suppresses_wrong_endpoint_receipt(vault,tmp_path,monkeypatch,index):
    save();wrong_finish(monkeypatch,index);path=tmp_path/'pacing.sqlite3'
    monkeypatch.setattr(portal,'_managed_path',lambda _:path)
    opener=Opener([account(),asset(),account()])
    with pytest.raises(ValueError,match='PAPER_PORTAL_ASSET_RULES_REVIEW_REQUIRED'):
        portal.inspect_rules_saved(expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50)
    gate=pacing.PaperReadPacing(path,ACCOUNT);before=path.read_bytes()
    assert len(opener.requests)==3 and gate.inspect()['last_outcome']=='SUCCESS'
    assert not pacing.fixed_targets_match(gate.operation_history()[0],'BTC_ASSET_RULES')
    assert path.read_bytes()==before


@pytest.mark.parametrize('index', range(3))
def test_quote_capture_refuses_before_retention_and_preserves_unbound_read(vault,tmp_path,monkeypatch,index):
    save();store,args=quote_args(tmp_path);wrong_finish(monkeypatch,index)
    with pytest.raises(ValueError,match='PACING_REQUEST_TARGET_DISAGREEMENT'):capture.capture(**args)
    assert not store.path.exists() and len(args['opener'].requests)==3
    gate=pacing.PaperReadPacing(args['pacing_path'],ACCOUNT)
    assert gate.inspect()['last_outcome']=='SUCCESS' and gate.inspect()['pending_token'] is None
    before=files(tmp_path);reads=vault.reads
    state=capture.status(**status_options(args))
    assert state['local_capture_history_agreement'] is False
    assert 'PACING_REQUEST_TARGET_UNVERIFIED' in state['review_reasons']
    with pytest.raises(ValueError,match='UNBOUND_HISTORY_REVIEW_REQUIRED'):capture.capture(**args)
    assert vault.reads==reads and files(tmp_path)==before


def retarget(value, index=None, profile=None):
    """Re-sign one synthetic copied session and its quote references completely."""
    assert len(value['pacing_events'])==3
    gate=pacing.PaperReadPacing('memory_only_unused',value['account_id'])
    with closing(bundle._memory('events','seq INTEGER PRIMARY KEY,body TEXT,previous TEXT,digest TEXT',value['pacing_events'])) as db:
        record=gate._load(db)['operation_sessions'][0]
    row=value['pacing_events'][-1];body=json.loads(row[1])
    if index is not None:
        body['request_sha256'][index]=sha(dict(method='GET',target='https://paper-api.alpaca.markets/v2/positions'))
    if profile in ('v1','v2'):
        body['receipt_profile']=pacing.RECEIPT_PROFILE_V1 if profile=='v1' else pacing.RECEIPT_PROFILE
        body.pop('result_projection_sha256',None)
        if profile=='v1':body.pop('request_sha256')
    scope=dict(profile=body['receipt_profile'],operation=record['operation'],
        before_head_sha256=record['before_head_sha256'],begin_head_sha256=record['begin_head_sha256'],
        evaluated_at=body['evaluated_at'],outcome=body['outcome'],request_kinds=body['request_kinds'],
        response_sha256=body['response_sha256'])
    if profile!='v1':scope['request_sha256']=body['request_sha256']
    if 'result_projection_sha256' in body:scope['result_projection_sha256']=body['result_projection_sha256']
    body['receipt_sha256']=sha(scope)
    row[1]=canonical(body);row[3]=sha(dict(seq=row[0],body=body,previous=row[2]))
    value['heads']['pacing']=row[3];previous='0'*64
    for row in value['quote_records']:
        body=json.loads(row[1])
        if row[0]:body['pacing_after_head_sha256']=value['heads']['pacing']
        row[1]=canonical(body);row[2]=previous;row[3]=sha(dict(seq=row[0],body=body,previous=previous));previous=row[3]
    value['heads']['quote']=previous
    value['bundle_sha256']=sha({k:v for k,v in value.items() if k!='bundle_sha256'})
    return value


@pytest.fixture
def copied(tmp_path):
    return bundle.loads(bundle.snapshot(**bundle._fixture(tmp_path/'private')))


@pytest.mark.parametrize('index',range(3))
def test_self_consistent_copied_wrong_endpoint_keeps_receipt_but_loses_agreement(copied,tmp_path,index):
    value=retarget(copied,index);before=files(tmp_path);result=bundle.validate(value)
    assert result['full_request_history_replayed'] is result['full_quote_history_replayed'] is result['full_pacing_history_replayed'] is True
    assert result['pacing_result_receipts_bound'] is True
    assert result['pacing_request_targets_bound'] is result['local_capture_history_agreement'] is False
    assert all(result[flag] is False for flag in bundle.FLAGS)
    with pytest.raises(ValueError,match='REVIEW_REQUIRED'):bundle.public_summary(result)
    assert files(tmp_path)==before


def test_v1_copied_receipt_remains_receipted_and_target_unverified(copied):
    result=bundle.validate(retarget(copied,profile='v1'))
    assert result['pacing_result_receipts_bound'] is True
    assert result['pacing_request_targets_bound'] is result['local_capture_history_agreement'] is False
    with pytest.raises(ValueError,match='REVIEW_REQUIRED'):bundle.public_summary(result)


def test_authenticated_ciphertext_does_not_promote_wrong_endpoint_copy(copied):
    value=retarget(copied,1);raw=canonical(value).encode()
    encrypted=bundle.seal(raw,PHRASE)
    result=bundle.validate(bundle.loads(bundle.unseal(encrypted,PHRASE)))
    assert result['pacing_result_receipts_bound'] is True and result['pacing_request_targets_bound'] is False
    assert result['local_capture_history_agreement'] is False


def test_live_historical_review_blocks_self_consistent_wrong_target_without_retry(vault,tmp_path):
    options=bundle._fixture(tmp_path/'private');value=retarget(bundle.loads(bundle.snapshot(**options)),1)
    # Alter only disposable synthetic evidence, preserving valid chains and
    # cross-journal heads so endpoint comparison is the independent blocker.
    with sqlite3.connect(options['pacing_path']) as db:
        db.execute('DELETE FROM events')
        db.executemany('INSERT INTO events VALUES (?,?,?,?)',value['pacing_events'])
    with sqlite3.connect(options['quote_store_path']) as db:
        db.execute('DELETE FROM records')
        db.executemany('INSERT INTO records VALUES (?,?,?,?)',value['quote_records'])
    keys=('expected_account_id','request_journal_path','quote_store_path','pacing_path','expected_request_head')
    args={k:options[k] for k in keys};before=files(tmp_path);reads=vault.reads
    result=capture.status(**args)
    assert result['review_reasons']==['PACING_REQUEST_TARGET_UNVERIFIED']
    assert result['local_capture_history_agreement'] is False
    opener=Opener()
    with pytest.raises(ValueError,match='UNBOUND_HISTORY_REVIEW_REQUIRED'):
        capture.capture(**args,expected_quote_head=value['heads']['quote'],
            expected_pacing_head=value['heads']['pacing'],client_order_id='nx_quote_fixture',
            confirm_read_only_paper=True,opener=opener)
    assert vault.reads==reads and not opener.requests and files(tmp_path)==before


def test_fresh_process_rejects_copied_endpoint_agreement_without_vault_or_network(copied):
    value=retarget(copied,1)
    script='''import json,sys
from nexus import paper_quote_bundle as bundle,paper_credentials
def audit(event,args):
 if event=='socket.connect':raise RuntimeError('NETWORK_DENIED')
def denied():raise RuntimeError('VAULT_DENIED')
sys.addaudithook(audit);paper_credentials._vault=denied
r=bundle.validate(bundle.loads(sys.stdin.buffer.read()))
print(json.dumps({k:r[k] for k in ('pacing_result_receipts_bound','pacing_request_targets_bound','local_capture_history_agreement')}))
'''
    result=subprocess.run([sys.executable,'-c',script],input=canonical(value),text=True,capture_output=True,timeout=20)
    assert result.returncode==0 and not result.stderr
    assert json.loads(result.stdout)==dict(pacing_result_receipts_bound=True,pacing_request_targets_bound=False,local_capture_history_agreement=False)


@pytest.mark.parametrize('operation',['ACTIVITY_COLLECTION','ORIGINAL_ID_RECOVERY','UNKNOWN',None,[],True])
def test_dynamic_or_unknown_purpose_is_not_given_fixed_target_acceptance(operation):
    assert pacing.fixed_targets_match({},operation) is False


@pytest.mark.parametrize('field',['three_fixed_targets_matched','wrong_fixed_targets_refused','v1_fixed_targets_unverified'])
def test_release_evidence_requires_each_fixed_target_check(field):
    from nexus import release_trials
    result=pacing.typed_fixture_report();assert result[field] is True;result[field]=False
    with pytest.raises(ValueError,match='TYPED_READ_OPERATIONS'):
        release_trials._validate('pacing',dict(passed=True,typed_operations=result))
