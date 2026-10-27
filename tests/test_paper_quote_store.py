"""Private original-prefix quote retention, failure boundaries and offline replay."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from nexus import paper_quote_store as subject,paper_quote_observation as observation
from nexus.alpaca_paper_contract import sha,preview
from test_paper_quote_observation import options,KEY,SECRET,ACCOUNT,OTHER


def setup(tmp_path):
    value=observation.inspect_quote(KEY,SECRET,**options(tmp_path))
    store=subject.PaperQuoteStore(tmp_path/'quotes.sqlite3',ACCOUNT,tmp_path/'requests.sqlite3')
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
        min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    plan=preview('nx_quote_fixture','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
    store.journal.prepare(plan)
    head=store.journal.recovery()['head_sha256']
    args=dict(client_order_id='nx_quote_fixture',expected_request_head=head,expected_quote_head=subject.ZERO)
    return store,value,args


def snapshot(root):return {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def digest(value):value['observation_sha256']=sha({k:value[k] for k in subject.SCOPE_KEYS})


def test_roundtrip_duplicate_restart_and_original_bytes(tmp_path):
    store,value,args=setup(tmp_path);before=store.request_path.read_bytes()
    result=store.retain(value,**args)
    assert result['duplicate'] is False
    args['expected_quote_head']=result['head_sha256'];saved=store.path.read_bytes()
    assert store.retain(value,**args)['duplicate'] is True
    assert store.path.read_bytes()==saved and store.request_path.read_bytes()==before
    fresh=subject.PaperQuoteStore(store.path,ACCOUNT,store.request_path)
    review=fresh.inspect(expected_request_head=args['expected_request_head'])
    assert review['records'][0]['observation']['quote']['exchange_time_ns']=='1792195200123456789'
    assert review['records'][0]['payload_sha256']==store.journal.recovery()['requests'][0]['payload_sha256']
    assert all(review[k] is False and result[k] is False for k in observation.FLAGS)
    assert set(tmp_path.iterdir())=={tmp_path/x for x in ('quotes.sqlite3','requests.sqlite3','pacing.sqlite3','pacing.sqlite3.owner.lock')}


def test_later_request_append_preserves_exact_historical_prefix(tmp_path):
    store,value,args=setup(tmp_path);store.retain(value,**args)
    store.journal.mark_submission_uncertain(args['client_order_id'])
    new_head=store.journal.recovery()['head_sha256']
    review=store.inspect(expected_request_head=new_head)
    assert review['records'][0]['request_head_sha256']==args['expected_request_head']
    assert review['request_head_sha256']==new_head
    with pytest.raises(ValueError,match='REQUEST_HEAD_CHANGED'):store.inspect(expected_request_head=args['expected_request_head'])


@pytest.mark.parametrize('field',observation.FLAGS)
def test_authority_promotion_refused_before_creation(tmp_path,field):
    store,value,args=setup(tmp_path);value[field]=True;before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='OBSERVATION_INVALID'):store.retain(value,**args)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('change',('foreign','digest','extra','crossed','age','time','ns','zero_bool','scope','elapsed','source'))
def test_bad_or_relabelled_observations_refused(tmp_path,change):
    store,value,args=setup(tmp_path)
    if change=='foreign':value['account_sha256']='1'*64
    if change=='digest':value['observation_sha256']='0'*64
    if change=='extra':value['quote']['private']='PRIVATE'
    if change=='crossed':value['quote']['bid']='102'
    if change=='age':value['quote']['sampled_age_ns']='0'
    if change=='time':value['read_timing']['finalized_at']+=30
    if change=='ns':value['quote']['exchange_time_ns']=int(value['quote']['exchange_time_ns'])
    if change=='zero_bool':value['fixed_get_requests']=True
    if change=='scope':value['timestamp_scope']='CLOCKS_VERIFIED'
    if change=='elapsed':value['read_timing']['elapsed_seconds']=float('nan')
    if change=='source':value['source']='VERIFIED_PROVIDER'
    if change!='digest':
        try:digest(value)
        except ValueError:pass
    before=snapshot(tmp_path)
    with pytest.raises((ValueError,TypeError)):store.retain(value,**args)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('which',('request','quote','identity'))
def test_missing_or_stale_scope_does_not_initialize(tmp_path,which):
    store,value,args=setup(tmp_path)
    args[{'request':'expected_request_head','quote':'expected_quote_head','identity':'client_order_id'}[which]]='f'*64
    before=snapshot(tmp_path)
    with pytest.raises(ValueError):store.retain(value,**args)
    assert snapshot(tmp_path)==before


def test_missing_inspection_is_read_only(tmp_path):
    store,value,args=setup(tmp_path);before=snapshot(tmp_path)
    review=store.inspect(expected_request_head=args['expected_request_head'])
    assert review['retained_records']==0 and review['head_sha256']==subject.ZERO
    assert snapshot(tmp_path)==before


def test_precommit_revocation_rolls_back_existing_store(tmp_path):
    store,value,args=setup(tmp_path);r=store.retain(value,**args)
    args['expected_quote_head']=r['head_sha256'];value=deepcopy(value)
    value['quote']['bid']='99';digest(value)
    before=store.path.read_bytes();calls=[]
    def revoke():calls.append(1);return len(calls)<2
    with pytest.raises(ValueError,match='INPUT_REVOKED'):store.retain(value,**args,precommit_check=revoke)
    assert store.path.read_bytes()==before


def test_lost_ack_retains_record_without_auto_retry(tmp_path):
    store,value,args=setup(tmp_path);calls=[]
    def revoke():calls.append(1);return len(calls)<3
    with pytest.raises(ValueError,match='INPUT_REVOKED_AFTER_COMMIT'):store.retain(value,**args,precommit_check=revoke)
    review=store.inspect(expected_request_head=args['expected_request_head'])
    assert review['retained_records']==1
    args['expected_quote_head']=review['head_sha256']
    assert store.retain(value,**args)['duplicate'] is True


def test_request_change_under_quote_transaction_rolls_back(tmp_path):
    store,value,args=setup(tmp_path);r=store.retain(value,**args)
    args['expected_quote_head']=r['head_sha256'];before=store.path.read_bytes();original=store._load
    def changed(db,requests):
        result=original(db,requests)
        store.journal.mark_submission_uncertain(args['client_order_id'])
        return result
    store._load=changed
    with pytest.raises(ValueError):store.retain(value,**args)
    assert store.path.read_bytes()==before


def test_capacity_preserves_all_history_and_duplicate_replay(tmp_path,monkeypatch):
    store,value,args=setup(tmp_path);r=store.retain(value,**args)
    args['expected_quote_head']=r['head_sha256'];before=store.path.read_bytes()
    monkeypatch.setattr(subject,'MAX_RECORDS',1)
    assert store.retain(value,**args)['duplicate'] is True
    value['quote']['bid']='99';digest(value)
    with pytest.raises(ValueError,match='RECORD_CAP'):store.retain(value,**args)
    assert store.path.read_bytes()==before


@pytest.mark.parametrize('kind',('chain','prefix','wal','hardlink','symlink','foreign'))
def test_corrupt_or_unsafe_history_is_never_repaired(tmp_path,kind):
    store,value,args=setup(tmp_path);store.retain(value,**args)
    if kind in ('chain','prefix'):
        with sqlite3.connect(store.path) as db:
            if kind=='chain':db.execute("UPDATE records SET digest=? WHERE seq=1",('0'*64,))
            else:
                body=json.loads(db.execute('SELECT body FROM records WHERE seq=1').fetchone()[0]);body['request_head_sha256']='f'*64
                previous=db.execute('SELECT previous FROM records WHERE seq=1').fetchone()[0]
                db.execute('UPDATE records SET body=?,digest=? WHERE seq=1',(subject.canonical(body),sha(dict(seq=1,body=body,previous=previous))))
    if kind=='wal':Path(str(store.path)+'-wal').write_bytes(b'RETAIN')
    if kind=='hardlink':Path(tmp_path/'alias').hardlink_to(store.path)
    if kind=='symlink':
        actual=tmp_path/'actual';store.path.rename(actual)
        try:store.path.symlink_to(actual)
        except OSError:pytest.skip('symlink privilege unavailable')
    if kind=='foreign':store=subject.PaperQuoteStore(store.path,OTHER,store.request_path)
    before=snapshot(tmp_path)
    with pytest.raises((ValueError,sqlite3.Error)):store.inspect(expected_request_head=args['expected_request_head'])
    assert snapshot(tmp_path)==before


def test_private_paths_and_distinct_inputs(tmp_path):
    with pytest.raises(ValueError):subject.PaperQuoteStore(Path(subject.__file__).parent/'quotes.sqlite3',ACCOUNT,tmp_path/'requests')
    with pytest.raises(ValueError):subject.PaperQuoteStore(tmp_path/'same',ACCOUNT,tmp_path/'same')


def test_fresh_process_replay_without_network_or_vault(tmp_path):
    store,value,args=setup(tmp_path);store.retain(value,**args);before=snapshot(tmp_path)
    script='''import json,socket,ssl,sys
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK'))
from nexus import paper_credentials
paper_credentials._capture=lambda:(_ for _ in ()).throw(AssertionError('VAULT'))
from nexus.paper_quote_store import PaperQuoteStore
r=PaperQuoteStore(sys.argv[1],sys.argv[2],sys.argv[3]).inspect(expected_request_head=sys.argv[4])
print(json.dumps(dict(count=r['retained_records'],routing=r['ready_for_routing'])))'''
    result=subprocess.run([sys.executable,'-c',script,str(store.path),ACCOUNT,str(store.request_path),args['expected_request_head']],capture_output=True,text=True,timeout=30)
    assert result.returncode==0 and json.loads(result.stdout)==dict(count=1,routing=False)
    assert snapshot(tmp_path)==before


def test_full_original_tail_validated_even_for_old_quote_prefix(tmp_path):
    store,value,args=setup(tmp_path);store.retain(value,**args)
    store.journal.mark_submission_uncertain(args['client_order_id']);head=store.journal.recovery()['head_sha256']
    with sqlite3.connect(store.request_path) as db:db.execute("UPDATE events SET digest=? WHERE seq=(SELECT max(seq) FROM events)",('f'*64,))
    before=snapshot(tmp_path)
    with pytest.raises(ValueError):store.inspect(expected_request_head=head)
    assert snapshot(tmp_path)==before


def test_prefix_before_prepare_cannot_bind_later_original(tmp_path):
    store,value,args=setup(tmp_path);store.retain(value,**args)
    with sqlite3.connect(store.request_path) as db:early=db.execute('SELECT digest FROM events WHERE seq=1').fetchone()[0]
    with sqlite3.connect(store.path) as db:
        body=json.loads(db.execute('SELECT body FROM records WHERE seq=1').fetchone()[0]);body['request_head_sha256']=early
        previous=db.execute('SELECT previous FROM records WHERE seq=1').fetchone()[0]
        db.execute('UPDATE records SET body=?,digest=? WHERE seq=1',(subject.canonical(body),sha(dict(seq=1,body=body,previous=previous))))
    before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='ORIGINAL_PREFIX_DISAGREEMENT'):store.inspect(expected_request_head=args['expected_request_head'])
    assert snapshot(tmp_path)==before


def test_request_change_after_commit_suppresses_ack_but_retains_quote(tmp_path,monkeypatch):
    store,value,args=setup(tmp_path);original=store._requests;calls=[]
    def changed(head):
        calls.append(1)
        if len(calls)==5:store.journal.mark_submission_uncertain(args['client_order_id'])
        return original(head)
    monkeypatch.setattr(store,'_requests',changed)
    with pytest.raises(ValueError,match='REQUEST_HEAD_CHANGED'):store.retain(value,**args)
    monkeypatch.setattr(store,'_requests',original)
    review=store.inspect(expected_request_head=store.journal.recovery()['head_sha256'])
    assert review['retained_records']==1


def test_byte_cap_refuses_before_history_changes(tmp_path,monkeypatch):
    store,value,args=setup(tmp_path);store.retain(value,**args);before=snapshot(tmp_path)
    monkeypatch.setattr(subject,'MAX_DATABASE_BYTES',1)
    with pytest.raises(ValueError,match='DATABASE_CAP'):store.inspect(expected_request_head=args['expected_request_head'])
    assert snapshot(tmp_path)==before


def test_record_size_cap_refuses_before_creation(tmp_path):
    store,value,args=setup(tmp_path);value['limitations']='x'*subject.MAX_RECORD_BYTES;before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='RECORD_CAP'):store.retain(value,**args)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('field,value,code',[
    ('original_prefix_bound',False,'BTC_QUOTE_RETENTION'),
    ('source_attested',True,'BTC_QUOTE_RETENTION_AUTHORITY'),
    ('ready_for_routing',True,'BTC_QUOTE_RETENTION_AUTHORITY')])
def test_mandatory_release_evidence_refuses_missing_checks_or_authority(field,value,code):
    from nexus import release_trials
    result=dict(passed=True,btc_quote_observation=observation.fixture_report(),btc_quote_retention=subject.fixture_report())
    result['btc_quote_retention'][field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):release_trials._validate('inspection_inventory',result)


def test_public_fixture_contains_only_aggregates():
    result=subject.fixture_report()
    assert result['passed'] is True and all(result[k] is True for k in subject.CHECKS)
    assert all(type(v) is bool or k=='source' for k,v in result.items())
    assert all(result[k] is False for k in observation.FLAGS)


@pytest.mark.parametrize('boundary',('before_commit','after_commit'))
def test_real_process_death_preserves_uncertain_boundary(tmp_path,boundary):
    store,value,args=setup(tmp_path);first=store.retain(value,**args)
    args['expected_quote_head']=first['head_sha256'];value['quote']['bid']='99';digest(value)
    data=tmp_path/'supplied.json';data.write_text(json.dumps(value))
    request_before=store.request_path.read_bytes()
    script='''import json,os,sys
from nexus.paper_quote_store import PaperQuoteStore
s=PaperQuoteStore(sys.argv[1],sys.argv[2],sys.argv[3])
v=json.load(open(sys.argv[4]));calls=[]
def check():
 calls.append(1)
 if len(calls)==int(sys.argv[7]):os._exit(73)
 return True
s.retain(v,client_order_id='nx_quote_fixture',expected_request_head=sys.argv[5],expected_quote_head=sys.argv[6],precommit_check=check)
'''
    result=subprocess.run([sys.executable,'-c',script,str(store.path),ACCOUNT,str(store.request_path),str(data),args['expected_request_head'],args['expected_quote_head'],'2' if boundary=='before_commit' else '3'],capture_output=True,timeout=30)
    assert result.returncode==73 and not result.stdout and not result.stderr
    before=snapshot(tmp_path)
    if boundary=='before_commit':
        assert Path(str(store.path)+'-journal').exists()
        with pytest.raises(ValueError,match='ROLLBACK_REVIEW_REQUIRED'):store.inspect(expected_request_head=args['expected_request_head'])
        with pytest.raises(ValueError,match='ROLLBACK_REVIEW_REQUIRED'):store.retain(value,**args)
    else:
        review=store.inspect(expected_request_head=args['expected_request_head'])
        assert review['retained_records']==2 and review['records'][1]['observation']['quote']['bid']=='99'
    assert snapshot(tmp_path)==before and store.request_path.read_bytes()==request_before


def contended_retain(tmp_path):
    store,value,args=setup(tmp_path);first=store.retain(value,**args)
    args['expected_quote_head']=first['head_sha256'];value=deepcopy(value)
    value['quote']['bid']='99';digest(value)
    return store,value,args


def test_writer_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    store,value,args=contended_retain(tmp_path);before=store.path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match=r'^PAPER_QUOTE_STORE_DATABASE_BUSY$'):
            store.retain(value,**args)
    assert store.path.read_bytes()==before
    assert not store.retain(value,**args)['duplicate']


def test_reader_blocked_commit_rolls_back_and_recovers(tmp_path,monkeypatch):
    store,value,args=contended_retain(tmp_path);before=store.path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN');blocker.execute('SELECT count(*) FROM records').fetchone()
        with pytest.raises(ValueError,match=r'^PAPER_QUOTE_STORE_DATABASE_BUSY$'):
            store.retain(value,**args)
    assert store.path.read_bytes()==before
    assert not store.retain(value,**args)['duplicate']


def test_read_contention_is_bounded_redacted_and_recovers(tmp_path,monkeypatch):
    store,_,args=contended_retain(tmp_path);expected=store.inspect(expected_request_head=args['expected_request_head'])
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ValueError,match=r'^PAPER_QUOTE_STORE_DATABASE_BUSY$'):
            store.inspect(expected_request_head=args['expected_request_head'])
    assert store.inspect(expected_request_head=args['expected_request_head'])==expected
