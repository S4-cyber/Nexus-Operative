"""Persisted original-request prefixes: complete tail replay, no source authority."""
import json
import sqlite3
import subprocess
import sys
import pytest
from nexus.alpaca_paper_contract import canonical,sha
from nexus import paper_activity_acceptance_probe as probe,paper_request_activity_acceptance as review
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_activity_acceptance_store import REQUEST_PROFILE,PaperActivityAcceptanceStore
from test_paper_request_collection import setup,append
from test_paper_activity_acceptance_probe import vault
from test_paper_credentials import save
from test_alpaca_paper_inspection import Opener,account,ACCOUNT,OTHER
from test_alpaca_activity_collection import NOW


def collected(root):
    args,journal,rows=setup(root)
    result=probe.probe(**args,opener=Opener([account(),rows,account()]))
    options={k:args[k] for k in ('expected_account_id','request_journal_path','pacing_path','activity_store_path','acceptance_store_path')}
    options.update(expected_request_head=args['expected_request_head'],expected_activity_head=result['local_retention']['after_head_sha256'])
    return options,journal,result,args,rows


def alter_scope(path,**fields):
    # Deliberately rewrite a valid local chain to test semantic cross-history
    # checks rather than only an easy broken-digest failure.
    with sqlite3.connect(path) as db:
        seq,raw,previous=db.execute('SELECT seq,body,previous FROM records WHERE seq=1').fetchone()
        body=json.loads(raw);body['scope'].update(fields);body['record_sha256']=sha(body['scope'])
        digest=sha(dict(seq=seq,body=body,previous=previous))
        db.execute('UPDATE records SET body=?,digest=? WHERE seq=?',(canonical(body),digest,seq))


def test_origin_survives_source_append_and_exact_historical_prefix_is_used(tmp_path,vault):
    save();options,journal,result,_,_=collected(tmp_path)
    scope=result['acceptance_record_scope']
    assert scope['profile']==REQUEST_PROFILE and scope['request_head_sha256']==options['expected_request_head']
    assert result['order_input']['request_head_persisted_in_acceptance_record'] is True
    original=review.review(**options)
    assert original['all_retained_request_origins_bound'] and original['retained_request_origin_prefixes_replayed']==1
    append(journal);options['expected_request_head']=journal.recovery()['head_sha256']
    before=journal.path.read_bytes();later=review.review(**options)
    assert later['all_retained_request_origins_bound'] and later['request_origin_bound_transitions']==1
    assert later['legacy_unbound_request_transitions']==0 and not later['local_request_activity_agreement']
    assert journal.path.read_bytes()==before and all(later[k] is False for k in review.FLAGS)


@pytest.mark.parametrize('forgery',['missing-prefix','existing-unobserved-prefix','wrong-order-digest'])
def test_rehashed_scope_cannot_invent_an_origin_or_substitute_orders(tmp_path,vault,forgery):
    save();options,journal,_,_,_=collected(tmp_path)
    if forgery=='wrong-order-digest':alter_scope(options['acceptance_store_path'],request_orders_sha256='f'*64)
    elif forgery=='missing-prefix':alter_scope(options['acceptance_store_path'],request_head_sha256='f'*64)
    else:
        with sqlite3.connect(journal.path) as db:head=db.execute('SELECT digest FROM events WHERE seq=1').fetchone()[0]
        alter_scope(options['acceptance_store_path'],request_head_sha256=head)
    before=journal.path.read_bytes()
    with pytest.raises(ValueError,match='ACCEPTED_HISTORY_REQUIRED|HISTORICAL_REQUEST_PREFIX_REQUIRED|HISTORICAL_REQUEST_INPUT_DISAGREEMENT'):
        review.review(**options)
    assert journal.path.read_bytes()==before


def test_prefix_read_validates_corrupt_current_tail_after_requested_point(tmp_path,vault):
    save();options,journal,_,_,_=collected(tmp_path);head=options['expected_request_head'];append(journal)
    with sqlite3.connect(journal.path) as db:db.execute("UPDATE events SET body='{}' WHERE seq=(SELECT MAX(seq) FROM events)")
    before=journal.path.read_bytes()
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):journal.observations_at_heads([head])
    assert journal.path.read_bytes()==before


def test_missing_prefix_storage_stays_missing_and_unknown_heads_are_reported(tmp_path):
    j=BrokerRequestJournal(tmp_path/'missing'/'r.sqlite3',ACCOUNT)
    r=j.observations_at_heads(['a'*64])
    assert r['missing_heads']==['a'*64] and not r['complete_current_tail_replayed'] and not j.path.parent.exists()
    assert not r['admission_reserved'] and not r['source_attested']


@pytest.mark.parametrize('heads',[None,'f'*64,{},[1],['not-a-head'],['f'*64]*129],ids=['none','string','dict','number','invalid','cap'])
def test_prefix_arguments_are_bounded_before_database_open(tmp_path,monkeypatch,heads):
    j=BrokerRequestJournal(tmp_path/'r.sqlite3',ACCOUNT)
    monkeypatch.setattr(j,'_connect',lambda *a,**kw:pytest.fail('database open'))
    with pytest.raises(ValueError,match='PREFIX_HEADS_INVALID'):j.observations_at_heads(heads)


def test_prefix_snapshots_are_detached_and_duplicates_do_not_mutate_history(tmp_path,vault):
    save();options,journal,_,_,_=collected(tmp_path);head=options['expected_request_head'];before=journal.path.read_bytes()
    r=journal.observations_at_heads([head,head]);r['observations_by_head'][head][0]['snapshot']['filled_quantity']='999'
    assert journal.observations_at_heads([head])['observations_by_head'][head][0]['snapshot']['filled_quantity']=='2'
    assert journal.path.read_bytes()==before
    with pytest.raises(ValueError,match='ACCOUNT_OR_SCHEMA'):BrokerRequestJournal(journal.path,OTHER).observations_at_heads([head])


def test_v2_history_is_preserved_and_not_relabelled_as_request_origin_bound(tmp_path):
    from nexus.paper_request_activity_acceptance import _fixture
    options,_=_fixture(tmp_path);before=tuple((p.name,p.read_bytes()) for p in sorted(tmp_path.iterdir()))
    result=review.review(**options)
    assert result['legacy_unbound_request_transitions']==1 and result['request_origin_bound_transitions']==0
    assert not result['all_retained_request_origins_bound'] and result['retained_request_origin_prefixes_replayed']==0
    assert before==tuple((p.name,p.read_bytes()) for p in sorted(tmp_path.iterdir()))


def test_mixed_v3_and_v2_inputs_replay_without_promoting_legacy_origin(tmp_path,vault):
    save();options,journal,result,args,_=collected(tmp_path)
    orders,_,_=probe._request_orders(journal.path,ACCOUNT,options['expected_request_head'])
    path=tmp_path/'orders.json';path.write_text(json.dumps(orders))
    file_args=dict(args,orders_path=path,request_journal_path=None,expected_request_head=None,wall_clock=lambda:NOW+60)
    second=probe.probe(**file_args,opener=Opener([account(),[],account()]))
    options['expected_activity_head']=second['local_retention']['after_head_sha256']
    r=review.review(**options)
    assert r['request_origin_bound_transitions']==1 and r['legacy_unbound_request_transitions']==1
    assert not r['all_retained_request_origins_bound'] and r['retained_request_origin_prefixes_replayed']==1


def test_actual_fresh_process_replays_origin_without_vault_or_network(tmp_path,vault):
    save();options,journal,_,_,_=collected(tmp_path);before=journal.path.read_bytes()
    script='''
import json,sys
from nexus import paper_credentials
from nexus.paper_request_activity_acceptance import review
def audit(event,args):
 if event=='socket.connect':raise RuntimeError('NETWORK_DENIED')
def denied():raise RuntimeError('VAULT_DENIED')
sys.addaudithook(audit);paper_credentials._vault=denied
r=review(**json.loads(sys.argv[1]))
print(json.dumps({'origins':r['request_origin_bound_transitions'],'replayed':r['retained_request_origin_prefixes_replayed'],'bound':r['all_retained_request_origins_bound']}))
'''
    encoded=json.dumps({k:str(v) for k,v in options.items()})
    child=subprocess.run([sys.executable,'-c',script,encoded],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)==dict(origins=1,replayed=1,bound=True) and journal.path.read_bytes()==before


def test_status_optionally_replays_all_request_origins_after_later_append(tmp_path,vault):
    save();options,journal,_,_,_=collected(tmp_path);append(journal)
    paths=(journal.path,options['pacing_path'],options['activity_store_path'],options['acceptance_store_path'])
    before=tuple(path.read_bytes() for path in paths);reads=vault.reads
    result=probe.status(expected_account_id=ACCOUNT,request_journal_path=journal.path,
        pacing_path=options['pacing_path'],activity_store_path=options['activity_store_path'],
        acceptance_store_path=options['acceptance_store_path'])
    assert result['request_journal_supplied'] and result['request_journal_present']
    assert result['request_origin_bound_transitions']==result['replayed_request_origin_transitions']==1
    assert result['retained_request_origin_prefixes_replayed']==1
    assert result['request_origin_missing_prefixes']==result['request_origin_input_disagreements']==0
    assert result['request_origin_current_tail_replayed'] and result['request_journal_rechecked_unchanged']
    assert result['request_origin_history_verified'] and result['all_retained_request_origins_bound']
    assert result['request_origin_current_head_sha256']==journal.recovery()['head_sha256']
    assert result['request_origin_recovery_state']=='REQUEST_ORIGIN_HISTORY_REPLAYED_REVIEW_REQUIRED'
    assert vault.reads==reads and before==tuple(path.read_bytes() for path in paths)


def test_status_does_not_promote_mixed_legacy_origin_history(tmp_path,vault):
    save();options,journal,_,args,_=collected(tmp_path)
    orders,_,_=probe._request_orders(journal.path,ACCOUNT,options['expected_request_head'])
    path=tmp_path/'orders.json';path.write_text(json.dumps(orders))
    second=probe.probe(**dict(args,orders_path=path,request_journal_path=None,
        expected_request_head=None,wall_clock=lambda:NOW+60),opener=Opener([account(),[],account()]))
    result=probe.status(expected_account_id=ACCOUNT,request_journal_path=journal.path,
        pacing_path=options['pacing_path'],activity_store_path=options['activity_store_path'],
        acceptance_store_path=options['acceptance_store_path'])
    assert second['acceptance_history']['records']==2
    assert result['request_origin_bound_transitions']==result['replayed_request_origin_transitions']==1
    assert result['legacy_unbound_request_transitions']==1
    assert not result['request_origin_history_verified'] and not result['all_retained_request_origins_bound']
    assert result['request_origin_recovery_state']=='LEGACY_REQUEST_ORIGIN_HISTORY_UNVERIFIED'


def test_status_reports_rehashed_origin_disagreement_without_promoting_it(tmp_path,vault):
    save();options,journal,_,_,_=collected(tmp_path)
    alter_scope(options['acceptance_store_path'],request_orders_sha256='f'*64)
    result=probe.status(expected_account_id=ACCOUNT,request_journal_path=journal.path,
        pacing_path=options['pacing_path'],activity_store_path=options['activity_store_path'],
        acceptance_store_path=options['acceptance_store_path'])
    assert result['request_origin_input_disagreements']==1
    assert result['replayed_request_origin_transitions']==0
    assert not result['request_origin_history_verified']
    assert result['request_origin_recovery_state']=='REQUEST_ORIGIN_HISTORY_DISAGREEMENT_REVIEW_REQUIRED'


def test_status_request_journal_must_be_distinct_and_cli_remains_vault_silent(tmp_path,vault,monkeypatch,capsys):
    save();options,journal,_,_,_=collected(tmp_path)
    before=options['activity_store_path'].read_bytes()
    with pytest.raises(ValueError,match='PAPER_ACTIVITY_ACCEPTANCE_PROBE_FAILED'):
        probe.status(expected_account_id=ACCOUNT,request_journal_path=options['activity_store_path'],
            pacing_path=options['pacing_path'],activity_store_path=options['activity_store_path'],
            acceptance_store_path=options['acceptance_store_path'])
    assert options['activity_store_path'].read_bytes()==before
    reads=vault.reads
    monkeypatch.setattr(sys,'argv',['tool','--expected-account',ACCOUNT,'--request-journal',str(journal.path),
        '--pacing',str(options['pacing_path']),'--activity-store',str(options['activity_store_path']),
        '--acceptance-store',str(options['acceptance_store_path']),'--status-only'])
    assert probe.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['request_origin_history_verified'] and vault.reads==reads


@pytest.mark.parametrize('change',['missing-head','missing-orders','invalid-head','extra-field'])
def test_v3_scope_requires_exact_bounded_request_fields_before_admission(tmp_path,vault,change):
    save();options,_,result,_,_=collected(tmp_path);scope=dict(result['acceptance_record_scope'])
    if change=='missing-head':scope.pop('request_head_sha256')
    elif change=='missing-orders':scope.pop('request_orders_sha256')
    elif change=='invalid-head':scope['request_head_sha256']='INVALID'
    else:scope['private-extra']='not admitted'
    store=PaperActivityAcceptanceStore(options['acceptance_store_path'],ACCOUNT);before=store.path.read_bytes()
    with pytest.raises(ValueError,match='SCOPE_INVALID|HEAD_INVALID'):
        store.append(scope,admission=lambda:pytest.fail('admission must not run'))
    assert store.path.read_bytes()==before
