"""Collect using retained original observations, injected saved pair and GETs."""
import json
import sys
import subprocess
import pytest
from nexus import paper_activity_acceptance_probe as probe,paper_credentials as credentials
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.broker_activity_store import BrokerActivityStore,_raw
from nexus.paper_read_pacing import PaperReadPacing
from nexus.paper_activity_acceptance_store import PaperActivityAcceptanceStore
from nexus.alpaca_paper_contract import preview
from test_paper_request_activity_acceptance import prepared
from test_paper_activity_acceptance_probe import vault
from test_paper_credentials import save
from test_alpaca_paper_inspection import Opener,account,ACCOUNT,OTHER
from test_alpaca_activity_collection import START,END,NOW


def setup(root):
    source=root/'source';source.mkdir();options,journal,activity=prepared(source)
    target=root/'collection'
    args=dict(expected_account_id=ACCOUNT,request_journal_path=journal.path,
        expected_request_head=options['expected_request_head'],pacing_path=target/'pacing.sqlite3',
        activity_store_path=target/'activity.sqlite3',acceptance_store_path=target/'acceptance.sqlite3',
        after=START,until=END,confirm_read_only_paper=True,wall_clock=lambda:NOW,elapsed_clock=lambda:50)
    rows=[_raw(row) for row in activity.inspect()['normalized_activities']]
    return args,journal,rows


def append(journal):
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    journal.prepare(preview('new_prepared','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001))


def test_retained_orders_remove_manual_file_and_produce_replayable_acceptance(tmp_path,vault):
    save();args,journal,rows=setup(tmp_path);before=journal.path.read_bytes();reader=Opener([account(),rows,account()])
    result=probe.probe(**args,opener=reader)
    assert len(reader.requests)==3 and all(req.method=='GET' for req,_ in reader.requests)
    assert result['collection']['counts']['order_snapshots']==1 and result['collection']['counts']['executions']==2
    assert result['order_input']['source']=='RETAINED_REQUEST_OBSERVATIONS' and result['order_input']['request_head_rechecked']
    assert result['order_input']['request_head_sha256']==args['expected_request_head']
    assert result['order_input']['request_head_persisted_in_acceptance_record'] is True
    assert journal.path.read_bytes()==before and not list(tmp_path.glob('**/orders.json'))
    state=probe.status(**{k:args[k] for k in ('expected_account_id','pacing_path','activity_store_path','acceptance_store_path')})
    assert state['all_accepted_transitions_replayed'] and state['accepted_transitions']==1
    assert all(result[k] is False for k in probe.FLAGS)
    assert all(row['id'] not in json.dumps(result) for row in rows)


@pytest.mark.parametrize('kind',['stale','missing','foreign','unobserved','pending','auth','no-head','both-inputs'])
def test_bad_request_source_denied_before_vault_network_or_destination_writes(tmp_path,vault,kind):
    args,journal,rows=setup(tmp_path)
    if kind=='stale':args['expected_request_head']='0'*64
    elif kind=='missing':args['request_journal_path']=tmp_path/'missing'/'r.sqlite3'
    elif kind=='foreign':args['expected_account_id']=OTHER
    elif kind=='unobserved':append(journal);journal.mark_submission_uncertain('new_prepared');args['expected_request_head']=journal.recovery()['head_sha256']
    elif kind in ('pending','auth'):
        other=BrokerRequestJournal(tmp_path/'source'/'other.sqlite3',ACCOUNT)
        original=journal.lookup_request('nx_accept_fixture')['plan'];other.prepare(original);other.mark_submission_uncertain('nx_accept_fixture')
        token=other.begin_lookup('nx_accept_fixture',evaluated_at=1000)['lookup_token']
        if kind=='auth':other.finish_lookup('nx_accept_fixture',token,evaluated_at=1001,status_code=401)
        args.update(request_journal_path=other.path,expected_request_head=other.recovery()['head_sha256'])
    elif kind=='no-head':args['expected_request_head']=None
    else:args['orders_path']=tmp_path/'orders.json'
    reader=Opener()
    with pytest.raises(ValueError):probe.probe(**args,opener=reader)
    assert vault.reads==0 and not reader.requests and not args['pacing_path'].parent.exists()


def test_request_alias_with_target_is_denied_before_any_write(tmp_path,vault):
    args,journal,_=setup(tmp_path);args['activity_store_path']=journal.path;before=journal.path.read_bytes()
    with pytest.raises(ValueError):probe.probe(**args,opener=Opener())
    assert journal.path.read_bytes()==before and vault.reads==0


@pytest.mark.parametrize('phase',['first-get','last-get','after-retention','before-retention-commit','before-acceptance-commit','after-acceptance'])
def test_request_changes_fence_io_and_commits_without_erasing_partial_evidence(tmp_path,vault,monkeypatch,phase):
    save();args,journal,rows=setup(tmp_path);reader=Opener([account(),rows,account()]);changed=False
    def race():
        nonlocal changed
        if not changed:append(journal);changed=True
    if phase in ('first-get','last-get'):
        original=reader.open
        def opened(req,timeout):
            result=original(req,timeout)
            if len(reader.requests)==(1 if phase=='first-get' else 3):race()
            return result
        reader.open=opened
    elif phase=='after-retention':
        original=BrokerActivityStore.retain
        def retained(self,*a,**kw):
            r=original(self,*a,**kw);race();return r
        monkeypatch.setattr(BrokerActivityStore,'retain',retained)
    elif phase=='before-retention-commit':
        # Existing valid empty history isolates receipt rollback from first-file
        # initialization rollback, whose retained empty file requires review.
        store=BrokerActivityStore(args['activity_store_path'],ACCOUNT)
        db=store._connect(write=True);db.commit();db.close()
        original=BrokerActivityStore._insert
        def inserted(db,*a,**kw):
            r=original(db,*a,**kw);race();return r
        monkeypatch.setattr(BrokerActivityStore,'_insert',staticmethod(inserted))
    elif phase=='before-acceptance-commit':
        original=PaperActivityAcceptanceStore._insert
        def accepted(db,seq,body,previous):
            r=original(db,seq,body,previous)
            if seq>0:race()
            return r
        monkeypatch.setattr(PaperActivityAcceptanceStore,'_insert',staticmethod(accepted))
    else:
        original=PaperActivityAcceptanceStore.append
        def accepted(self,*a,**kw):
            r=original(self,*a,**kw);race();return r
        monkeypatch.setattr(PaperActivityAcceptanceStore,'append',accepted)
    with pytest.raises(ValueError):probe.probe(**args,opener=reader)
    assert changed and len(reader.requests)==(1 if phase=='first-get' else 3)
    retained=BrokerActivityStore(args['activity_store_path'],ACCOUNT).inspect()
    accepted=PaperActivityAcceptanceStore(args['acceptance_store_path'],ACCOUNT).inspect()
    pending=PaperReadPacing(args['pacing_path'],ACCOUNT).inspect()['pending_token'] is not None
    assert retained['retained_batches']==(1 if phase in ('after-retention','before-acceptance-commit','after-acceptance') else 0)
    assert accepted['records']==(1 if phase=='after-acceptance' else 0)
    assert pending is (phase not in ('before-acceptance-commit','after-acceptance'))
    assert len(journal.recovery()['requests'])==2


def test_guard_denial_precedes_saved_pair_capture(tmp_path,vault):
    with pytest.raises(ValueError):credentials.collect_saved([],expected_account_id=ACCOUNT,after=START,until=END,
        confirm_read_only_paper=True,input_guard=lambda:False)
    assert vault.reads==0


def test_cli_rejects_incomplete_or_status_conflicting_request_inputs(tmp_path):
    args,_,_=setup(tmp_path)
    base=[sys.executable,'-m','nexus.paper_activity_acceptance_probe','--expected-account',ACCOUNT,
        '--pacing',str(args['pacing_path']),'--activity-store',str(args['activity_store_path']),
        '--acceptance-store',str(args['acceptance_store_path'])]
    for extra in (['--request-journal','PRIVATE_PATH','--after',START,'--until',END],
                  ['--request-journal','PRIVATE_PATH','--request-head','f'*64,'--status-only']):
        child=subprocess.run(base+extra,capture_output=True,text=True,timeout=10)
        assert child.returncode==2 and child.stdout=='PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED\n' and not child.stderr
    assert not args['pacing_path'].parent.exists()


def test_source_cli_can_use_retained_orders_without_an_order_file(tmp_path,vault,monkeypatch,capsys):
    save();args,_,rows=setup(tmp_path);reader=Opener([account(),rows,account()]);original=probe.probe
    def injected(**kw):return original(**kw,opener=reader,wall_clock=lambda:NOW,elapsed_clock=lambda:50)
    monkeypatch.setattr(probe,'probe',injected)
    command=['fixture','--confirm-read-only-paper','--expected-account',ACCOUNT,'--after',START,'--until',END]
    for flag,key in (('request-journal','request_journal_path'),('request-head','expected_request_head'),('pacing','pacing_path'),('activity-store','activity_store_path'),('acceptance-store','acceptance_store_path')):command+=['--'+flag,str(args[key])]
    monkeypatch.setattr(sys,'argv',command)
    assert probe.main()==0
    report=json.loads(capsys.readouterr().out)
    assert report['order_input']['source']=='RETAINED_REQUEST_OBSERVATIONS' and len(reader.requests)==3
