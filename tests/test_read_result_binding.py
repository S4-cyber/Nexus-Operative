"""Normalized outputs must match the same retained read, never source authority."""
from copy import deepcopy
import json
import sqlite3

import pytest

from nexus import alpaca_paper_inspection as reader
from nexus import paper_credentials as credentials
from nexus import paper_acceptance_probe as inventory
from nexus import paper_portal_probe as portal
from nexus import paper_asset_rules as assets
from nexus import paper_quote_bundle as bundle
from nexus import paper_quote_capture as capture
from nexus import paper_read_pacing as pacing
from nexus import paper_read_projection as projection
from nexus import paper_activity_acceptance_probe as activity
from nexus import paper_activity_acceptance_evidence as recovery
from nexus import paper_reconciliation_acceptance as balances
from nexus import paper_request_activity_acceptance as requests
from nexus.alpaca_paper_contract import sha, canonical
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore
from test_paper_credentials import vault, save
from test_alpaca_paper_inspection import ACCOUNT, KEY, SECRET, Opener, account, position
from test_paper_asset_rules import asset
from test_paper_open_order_terms import order
from test_paper_acceptance_probe import run as account_read
from test_paper_activity_acceptance_probe import run as activity_read
from test_paper_quote_capture import args as quote_args
from test_paper_quote_bundle import files, PHRASE
from test_fixed_request_targets import retarget
from test_typed_paper_read_pacing import receipt as old_receipt


@pytest.mark.parametrize('change',['cash','buying_power','position','orders','export_only'])
def test_inventory_refuses_a_different_valid_report_or_export(vault,tmp_path,monkeypatch,change):
    save();before=account();after=account();positions=[position()];orders=[]
    if change in ('cash','export_only'):before['cash']=after['cash']='999'
    if change=='buying_power':before['non_marginable_buying_power']=after['non_marginable_buying_power']='899'
    if change=='position':positions=[position(qty='3',qty_available='2.9')]
    if change=='orders':orders=[order()]
    alternate=reader.inspect_account(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,
        opener=Opener([before,positions,orders,after]),wall_clock=lambda:1000,elapsed_clock=lambda:50)
    original=credentials.inspect_saved
    def changed(**kwargs):
        result=original(**kwargs)
        return dict(result,portable_observation=alternate['portable_observation']) if change=='export_only' else alternate
    monkeypatch.setattr(credentials,'inspect_saved',changed)
    path=tmp_path/'pacing.sqlite3'
    with pytest.raises(ValueError,match='PAPER_ACCEPTANCE_PROBE_FAILED'):account_read(path)
    gate=pacing.PaperReadPacing(path,ACCOUNT);before_bytes=path.read_bytes()
    assert gate.inspect()['last_outcome']=='SUCCESS' and gate.inspect()['pending_token'] is None
    record=gate.operation_history()[0]
    assert record['normalized_result_bound'] is True
    assert path.read_bytes()==before_bytes


@pytest.mark.parametrize('change',[{'min_order_size':'0.0002'},{'price_increment':'2'},{'tradable':False}])
def test_asset_consumer_refuses_different_valid_normalized_rules(vault,tmp_path,monkeypatch,change):
    save();path=tmp_path/'pacing.sqlite3';monkeypatch.setattr(portal,'_managed_path',lambda _:path)
    alternate=assets.inspect_rules(KEY,SECRET,confirm_read_only_paper=True,expected_account_id=ACCOUNT,
        opener=Opener([account(),asset(**change),account()]),wall_clock=lambda:1000,elapsed_clock=lambda:50)
    original=credentials.inspect_rules_saved
    def changed(**kwargs):original(**kwargs);return alternate
    monkeypatch.setattr(credentials,'inspect_rules_saved',changed)
    with pytest.raises(ValueError,match='ASSET_RULES_REVIEW_REQUIRED'):
        portal.inspect_rules_saved(expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            opener=Opener([account(),asset(),account()]),wall_clock=lambda:1000,elapsed_clock=lambda:50)
    assert pacing.PaperReadPacing(path,ACCOUNT).inspect()['last_outcome']=='SUCCESS'


@pytest.mark.parametrize('change',['bid','size','source','account_digest'])
def test_quote_cannot_retain_a_different_self_consistent_normalized_observation(vault,tmp_path,monkeypatch,change):
    save();store,args=quote_args(tmp_path);original=credentials._consume_quote_saved
    def changed(**kwargs):
        consume=kwargs['consumer']
        def replace(value,check):
            value=deepcopy(value)
            if change=='bid':value['quote']['bid']='99'
            if change=='size':value['quote']['ask_size']='4'
            if change=='source':value['source']='ALPACA_HTTPS_READS'
            if change=='account_digest':value['account_fields_sha256']='f'*64
            value['observation_sha256']=sha({k:value[k] for k in projection.QUOTE_SCOPE})
            return consume(value,check)
        return original(**dict(kwargs,consumer=replace))
    monkeypatch.setattr(credentials,'_consume_quote_saved',changed)
    with pytest.raises(ValueError,match='PACING_NORMALIZED_RESULT_DISAGREEMENT'):capture.capture(**args)
    assert not store.path.exists() and len(args['opener'].requests)==3
    assert pacing.PaperReadPacing(args['pacing_path'],ACCOUNT).inspect()['last_outcome']=='SUCCESS'


def changed_quote_copy(root):
    options=bundle._fixture(root);value=bundle.loads(bundle.snapshot(**options));previous='0'*64
    for row in value['quote_records']:
        body=json.loads(row[1])
        if row[0]:body['observation']['quote']['bid']='99'
        row[1]=canonical(body);row[2]=previous;row[3]=sha(dict(seq=row[0],body=body,previous=previous));previous=row[3]
    value['heads']['quote']=previous;value['bundle_sha256']=sha({k:v for k,v in value.items() if k!='bundle_sha256'})
    return options,value


def test_self_consistent_copied_quote_and_real_ciphertext_do_not_promote_a_different_result(tmp_path):
    _,value=changed_quote_copy(tmp_path/'private');before=files(tmp_path)
    result=bundle.validate(value)
    assert result['full_quote_history_replayed'] is result['full_pacing_history_replayed'] is True
    assert result['pacing_result_receipts_bound'] is result['pacing_request_targets_bound'] is True
    assert result['pacing_normalized_results_bound'] is result['local_capture_history_agreement'] is False
    with pytest.raises(ValueError,match='REVIEW_REQUIRED'):bundle.public_summary(result)
    encrypted=bundle.seal(canonical(value).encode(),PHRASE)
    again=bundle.validate(bundle.loads(bundle.unseal(encrypted,PHRASE)))
    assert again['local_capture_history_agreement'] is False and files(tmp_path)==before


def test_v2_copy_stays_receipted_and_target_bound_but_normalized_result_unverified(tmp_path):
    options=bundle._fixture(tmp_path/'private');value=retarget(bundle.loads(bundle.snapshot(**options)),profile='v2')
    before=files(tmp_path);result=bundle.validate(value)
    assert result['pacing_result_receipts_bound'] is result['pacing_request_targets_bound'] is True
    assert result['pacing_normalized_results_bound'] is result['local_capture_history_agreement'] is False
    assert files(tmp_path)==before


def test_live_quote_review_blocks_changed_result_without_repair(tmp_path):
    options,value=changed_quote_copy(tmp_path/'private')
    with sqlite3.connect(options['quote_store_path']) as db:
        db.execute('DELETE FROM records');db.executemany('INSERT INTO records VALUES (?,?,?,?)',value['quote_records'])
    before=files(tmp_path)
    keys=('expected_account_id','request_journal_path','quote_store_path','pacing_path','expected_request_head')
    state=capture.status(**{k:options[k] for k in keys})
    assert state['review_reasons']==['PACING_NORMALIZED_RESULT_UNVERIFIED']
    assert state['local_capture_history_agreement'] is False and files(tmp_path)==before


@pytest.mark.parametrize('legacy',[True,False])
def test_activity_requires_matching_normalized_result_across_all_history(tmp_path,monkeypatch,legacy):
    if not legacy:
        original=pacing.PaperReadPacing.finish
        def changed(self,token,**kwargs):
            receipt=dict(kwargs['receipt'],result_projection_sha256='f'*64)
            return original(self,token,**dict(kwargs,receipt=receipt))
        with monkeypatch.context() as patch:
            patch.setattr(pacing.PaperReadPacing,'finish',changed)
            gate,store,accepted,scope=recovery._scope(tmp_path)
    else:gate,store,accepted,scope=recovery._scope(tmp_path,bind_normalized=False)
    accepted.append(scope,admission=lambda:(gate.inspect()['head_sha256'],store.inspect()['head_sha256']))
    # A new valid tail cannot hide an older v2 result or a v3 disagreement.
    gate,store,_,scope=recovery._scope(tmp_path,2)
    accepted.append(scope,admission=lambda:(gate.inspect()['head_sha256'],store.inspect()['head_sha256']))
    args=dict(expected_account_id=ACCOUNT,pacing_path=gate.path,activity_store_path=store.path,acceptance_store_path=accepted.path)
    before=recovery._sqlite_files_snapshot(tmp_path);state=activity.status(**args)
    assert state['latest_acceptance_record_replayed'] is state['all_pacing_request_targets_bound'] is True
    assert state['all_pacing_normalized_results_bound'] is False
    assert state['cross_journal_recovery_state']=='ACCEPTED_NORMALIZED_RESULT_UNVERIFIED_REVIEW_REQUIRED'
    for review,extra in ((balances.review,dict(opening_observation={},closing_observation={})),
        (requests.review,dict(request_journal_path=tmp_path/'absent.sqlite3',expected_request_head='0'*64))):
        with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED'):
            review(**args,expected_activity_head=store.inspect()['head_sha256'],**extra)
    assert recovery._sqlite_files_snapshot(tmp_path)==before


def test_activity_probe_preserves_unbound_read_on_normalized_result_disagreement(vault,tmp_path,monkeypatch):
    save();original=pacing.PaperReadPacing.finish
    def changed(self,token,**kwargs):
        return original(self,token,**dict(kwargs,receipt=dict(kwargs['receipt'],result_projection_sha256='f'*64)))
    monkeypatch.setattr(pacing.PaperReadPacing,'finish',changed)
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED'):activity_read(tmp_path)
    root=tmp_path/'private'
    assert PaperActivityAcceptanceStore(root/'acceptance.sqlite3',ACCOUNT).inspect()['records']==0
    assert pacing.PaperReadPacing(root/'pacing.sqlite3',ACCOUNT).inspect()['last_outcome']=='SUCCESS'


@pytest.mark.parametrize('when',['before_commit','after_commit'])
def test_normalized_result_mutation_suppresses_ack_and_preserves_commit_boundary(vault,tmp_path,monkeypatch,when):
    save();bound=[];original_bind=reader._ReadSession.bind_result_projection
    def bind(self,value):original_bind(self,value);bound.append(value)
    monkeypatch.setattr(reader._ReadSession,'bind_result_projection',bind)
    if when=='before_commit':
        original=pacing.PaperReadPacing._append
        def append(db,seq,body,parent):
            result=original(db,seq,body,parent)
            if body.get('kind')=='RESULT':bound[-1]['observed_inventory']['cash']='999'
            return result
        monkeypatch.setattr(pacing.PaperReadPacing,'_append',staticmethod(append))
    else:
        original=reader._ReadSession.checkpoint
        def checkpoint(self):
            if self.pacing_finished and bound:bound[-1]['observed_inventory']['cash']='999'
            return original(self)
        monkeypatch.setattr(reader._ReadSession,'checkpoint',checkpoint)
    path=tmp_path/'pacing.sqlite3'
    with pytest.raises(ValueError,match='RESULT_BINDING_CHANGED'):account_read(path)
    gate=pacing.PaperReadPacing(path,ACCOUNT);state=gate.inspect()
    assert (state['pending_token'] is not None)==(when=='before_commit')
    assert state['events']==(1 if when=='before_commit' else 2)
    if when=='after_commit':assert state['last_outcome']=='SUCCESS'


@pytest.mark.parametrize('bad',[None,True,'a'*63,'g'*64,[]])
def test_invalid_projection_cannot_complete_a_typed_reservation(tmp_path,bad):
    gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1000,ownership=owner,operation='ACCOUNT_INVENTORY')
        before=gate.path.read_bytes()
        with pytest.raises(ValueError,match='RECEIPT_PROJECTION_INVALID'):
            gate.finish(token,admission=lambda:1001,status_code=200,ownership=owner,
                receipt=dict(old_receipt('ACCOUNT_INVENTORY'),result_projection_sha256=bad))
        assert gate.path.read_bytes()==before and gate.inspect()['pending_token']==token


def test_v3_large_fixed_receipt_is_bounded_private_and_replays_cold(vault,tmp_path):
    save();path=tmp_path/'pacing.sqlite3';account_read(path,at=1792195200.1234567)
    with sqlite3.connect(path) as db:encoded=db.execute('SELECT body FROM events WHERE seq=2').fetchone()[0]
    assert pacing.LEGACY_MAX_EVENT_BYTES<len(encoded.encode())<=pacing.MAX_EVENT_BYTES
    assert all(secret not in encoded for secret in (KEY,SECRET,ACCOUNT,'cash','qty_available'))
    gate=pacing.PaperReadPacing(path,ACCOUNT);before=path.read_bytes();record=gate.operation_history()[0]
    assert record['receipt_profile']==pacing.RECEIPT_PROFILE_V3 and record['normalized_result_bound'] is True
    assert path.read_bytes()==before


def test_legacy_typed_results_remain_readable_without_normalized_result_invention(tmp_path):
    gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1000,ownership=owner,operation='ACCOUNT_INVENTORY')
        gate.finish(token,admission=lambda:1001,status_code=200,ownership=owner,receipt=old_receipt('ACCOUNT_INVENTORY'))
    before=gate.path.read_bytes();record=gate.operation_history()[0]
    assert record['receipt_profile']==pacing.RECEIPT_PROFILE and record['result_receipt_bound'] is True
    assert record['normalized_result_bound'] is False and record['result_projection_sha256'] is None
    assert projection.matches(record,'ACCOUNT_INVENTORY',{}) is False and gate.path.read_bytes()==before


def test_all_five_readers_and_release_evidence_require_normalized_binding():
    from nexus import release_trials
    result=pacing.typed_fixture_report();assert result['five_normalized_results_bound'] is True
    result['five_normalized_results_bound']=False
    with pytest.raises(ValueError,match='TYPED_READ_OPERATIONS'):
        release_trials._validate('pacing',dict(passed=True,typed_operations=result))


@pytest.mark.parametrize('change,code',[('digest','RECEIPT_DIGEST'),('profile','RECEIPT_INVALID'),('missing','RECEIPT_INVALID')])
def test_projection_tamper_refuses_complete_replay_without_repair(vault,tmp_path,change,code):
    save();path=tmp_path/'pacing.sqlite3';account_read(path)
    with sqlite3.connect(path) as db:
        encoded,parent=db.execute('SELECT body,previous FROM events WHERE seq=2').fetchone();body=json.loads(encoded)
        if change=='digest':body['result_projection_sha256']='f'*64
        elif change=='profile':body['receipt_profile']=pacing.RECEIPT_PROFILE
        else:body.pop('result_projection_sha256')
        db.execute('UPDATE events SET body=?,digest=? WHERE seq=2',(canonical(body),sha(dict(seq=2,body=body,previous=parent))))
    before=path.read_bytes();reads=vault.reads
    with pytest.raises(ValueError,match=code):pacing.PaperReadPacing(path,ACCOUNT).operation_history()
    assert path.read_bytes()==before and vault.reads==reads


def test_failed_read_cannot_claim_a_successful_normalized_result(tmp_path):
    gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1000,ownership=owner,operation='ACCOUNT_INVENTORY')
        before=gate.path.read_bytes()
        with pytest.raises(ValueError,match='RECEIPT_PROJECTION_INVALID'):
            gate.finish(token,admission=lambda:1001,status_code=429,ownership=owner,
                receipt=dict(old_receipt('ACCOUNT_INVENTORY','RATE_LIMITED'),result_projection_sha256='a'*64))
        assert gate.path.read_bytes()==before and gate.inspect()['pending_token']==token
