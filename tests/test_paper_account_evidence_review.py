"""Private composed review: mismatches, races and CLI privacy, no actual IO."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from nexus import paper_account_evidence_review as subject
from nexus import paper_observation_review as observations
from nexus import paper_credentials
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.alpaca_paper_contract import canonical,preview
from test_paper_request_activity_acceptance import prepared

EPOCH=1792195200+18*3600


def options(root,prices=('98','100')):
    result,_,_=prepared(root,prices)
    account=result['expected_account_id']
    return dict(**result,
        opening_observation=observations._fixture_observation(account,'1000','0',EPOCH-2,EPOCH-1),
        closing_observation=observations._fixture_observation(account,'802','2',EPOCH+3,EPOCH+4))


def snapshot(root):return tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()) if p.is_file())


def test_complete_bundle_reuses_one_history_without_writes_or_authority(tmp_path,monkeypatch):
    args=options(tmp_path);before=snapshot(tmp_path)
    monkeypatch.setattr(paper_credentials,'_vault',lambda:pytest.fail('vault access'))
    r=subject.review(**args)
    assert r['component_heads_agree'] and r['journals_rechecked_unchanged'] and r['complete_accepted_history_replayed']
    assert r['local_request_terms_agree'] and r['inventory_arithmetic_evaluated']
    assert r['original_requests_reviewed']==1 and r['explicit_fills_reviewed']==2
    assert r['gross_only_ending_balances']=={'USD':'802','BTC':'2'}
    assert r['gross_only_minus_observed']=={'USD':'0','BTC':'0'}
    assert r['retained_fee_record_counts']=={'USD':0,'BTC':0}
    assert all(r[k] is False for k in subject.FLAGS) and r['status']=='REVIEW_REQUIRED'
    assert snapshot(tmp_path)==before and subject.review(**args)==r


def test_good_aggregate_balance_cannot_hide_individual_fill_limit_violation(tmp_path):
    args=options(tmp_path,prices=('85','113'));r=subject.review(**args)
    assert r['gross_only_minus_observed']=={'USD':'0','BTC':'0'}
    assert not r['local_request_terms_agree'] and 'INDIVIDUAL_FILL_LIMIT_DISAGREEMENT' in r['request_agreement_blockers']
    assert not r['ready_for_routing'] and not r['broker_account_reconciled']


def test_balance_mismatch_preserved_independently_of_good_request_terms(tmp_path):
    args=options(tmp_path);args['closing_observation']=observations._fixture_observation(args['expected_account_id'],'803','2',EPOCH+3,EPOCH+4)
    r=subject.review(**args)
    assert r['local_request_terms_agree'] and r['gross_only_minus_observed']=={'USD':'-1','BTC':'0'}
    assert not r['fee_coverage_complete'] and not r['spendable_funds_verified']


def test_retained_fill_during_observation_keeps_arithmetic_unavailable(tmp_path):
    args=options(tmp_path);args['opening_observation']=observations._fixture_observation(args['expected_account_id'],'1000','0',EPOCH+1,EPOCH+1.5)
    r=subject.review(**args)
    assert r['local_request_terms_agree'] and not r['inventory_arithmetic_evaluated']
    assert r['gross_only_minus_observed'] is None and r['gross_only_debits_covered'] is None
    assert 'RETAINED_FILL_DURING_SEQUENTIAL_OBSERVATION' in r['review_reasons']


@pytest.mark.parametrize('field',['expected_request_head','expected_activity_head'])
def test_stale_pinned_heads_refuse_without_writes(tmp_path,field):
    args=options(tmp_path);args[field]='0'*64;before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT'):subject.review(**args)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('field',['pacing_head_sha256','acceptance_head_sha256','account_sha256'])
def test_component_cannot_substitute_another_head_or_account(tmp_path,monkeypatch,field):
    args=options(tmp_path);original=subject.inventory.review
    def altered(**kw):
        r=original(**kw);r[field]='f'*64;return r
    monkeypatch.setattr(subject.inventory,'review',altered)
    with pytest.raises(ValueError,match='COMPONENT_HEAD_DISAGREEMENT'):subject.review(**args)


@pytest.mark.parametrize('flag',['fee_coverage_complete','ready_for_routing','spendable_funds_verified'])
def test_component_authority_promotion_is_rejected(tmp_path,monkeypatch,flag):
    args=options(tmp_path);original=subject.requests.review
    def altered(**kw):
        r=original(**kw);r[flag]=True;return r
    monkeypatch.setattr(subject.requests,'review',altered)
    with pytest.raises(ValueError,match='COMPONENT_AUTHORITY_DISAGREEMENT'):subject.review(**args)


@pytest.mark.parametrize('kind',['requests','pacing'])
def test_append_after_last_component_is_detected_without_repair(tmp_path,monkeypatch,kind):
    args=options(tmp_path);original=subject.inventory.review
    def append(**kw):
        r=original(**kw)
        if kind=='pacing':
            from nexus.paper_read_pacing import PaperReadPacing
            PaperReadPacing(args['pacing_path'],args['expected_account_id']).begin(admission=lambda:1060)
        else:
            journal=BrokerRequestJournal(args['request_journal_path'],args['expected_account_id'])
            asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
            journal.prepare(preview('new_prepared','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001))
        return r
    monkeypatch.setattr(subject.inventory,'review',append)
    with pytest.raises(ValueError,match='JOURNALS_CHANGED_DURING_REVIEW'):subject.review(**args)
    if kind=='requests':assert len(BrokerRequestJournal(args['request_journal_path'],args['expected_account_id']).recovery()['requests'])==2


def test_missing_history_stays_absent(tmp_path):
    root=tmp_path/'missing';account='01234567-89ab-cdef-0123-456789abcdef'
    with pytest.raises(ValueError,match='COMPLETE_ACCEPTED_HISTORY_REQUIRED'):
        subject.review(expected_account_id=account,request_journal_path=root/'requests.sqlite3',pacing_path=root/'pacing.sqlite3',activity_store_path=root/'activity.sqlite3',acceptance_store_path=root/'acceptance.sqlite3',expected_request_head='0'*64,expected_activity_head='0'*64,opening_observation=observations._fixture_observation(account,'1000','0',EPOCH-2,EPOCH-1),closing_observation=observations._fixture_observation(account,'1000','0',EPOCH+3,EPOCH+4))
    assert not root.exists()


def test_aliased_journals_refuse_before_replay(tmp_path,monkeypatch):
    args=options(tmp_path);args['pacing_path']=args['activity_store_path']
    monkeypatch.setattr(subject,'acceptance_status',lambda **kw:pytest.fail('replay should not run'))
    with pytest.raises(ValueError,match='DISTINCT_JOURNALS_REQUIRED'):subject.review(**args)


def command(args,opening,closing):
    result=[]
    for flag,key in (('expected-account','expected_account_id'),('request-journal','request_journal_path'),('pacing','pacing_path'),('activity-store','activity_store_path'),('acceptance-store','acceptance_store_path'),('request-head','expected_request_head'),('activity-head','expected_activity_head')):
        result+=['--'+flag,str(args[key])]
    return result+['--opening',str(opening),'--closing',str(closing)]


def test_actual_fresh_cli_is_network_vault_silent_and_preserves_files(tmp_path):
    args=options(tmp_path);opening=tmp_path/'opening.json';closing=tmp_path/'closing.json'
    opening.write_text(canonical(args['opening_observation']));closing.write_text(canonical(args['closing_observation']))
    before=snapshot(tmp_path)
    script='''
import sys
from nexus import paper_credentials
from nexus.paper_account_evidence_review import main
def audit(event,args):
 if event=='socket.connect':raise RuntimeError('NETWORK_DENIED')
def denied():raise RuntimeError('VAULT_DENIED')
sys.addaudithook(audit);paper_credentials._vault=denied
sys.argv=['fixture']+sys.argv[1:]
raise SystemExit(main())
'''
    child=subprocess.run([sys.executable,'-c',script,*command(args,opening,closing)],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    r=json.loads(child.stdout);assert r['local_request_terms_agree'] and r['ready_for_routing'] is False
    assert snapshot(tmp_path)==before


def test_encrypted_cli_inputs_stay_in_memory_and_output_is_private(tmp_path,monkeypatch,capsys):
    from nexus import encrypted_observation as encryption
    import getpass
    args=options(tmp_path);phrase='fixture phrase for in-memory review only'
    paths=[tmp_path/'opening.nexusenc',tmp_path/'closing.nexusenc']
    for p,k in zip(paths,('opening_observation','closing_observation')):p.write_bytes(encryption.seal(canonical(args[k]).encode(),phrase))
    before=snapshot(tmp_path);monkeypatch.setattr(getpass,'getpass',lambda *a,**k:phrase)
    monkeypatch.setattr(sys,'argv',['fixture',*command(args,*paths),'--opening-encrypted','--closing-encrypted'])
    assert subject.main()==0
    r=json.loads(capsys.readouterr().out);assert r['gross_only_minus_observed']=={'USD':'0','BTC':'0'}
    assert snapshot(tmp_path)==before


def test_cli_bad_input_and_unknown_secret_argument_do_not_echo_private_values(tmp_path):
    args=options(tmp_path);p=tmp_path/'PRIVATE_FIXTURE_PATH.json';p.write_text('PRIVATE_FIXTURE_CONTENT')
    base=[sys.executable,'-m','nexus.paper_account_evidence_review',*command(args,p,tmp_path/'missing.json')]
    child=subprocess.run(base,capture_output=True,text=True,timeout=10)
    assert child.returncode==2 and child.stdout=='PAPER_ACCOUNT_EVIDENCE_FAILED\n' and not child.stderr
    child=subprocess.run(base+['--api-secret','PRIVATE_FIXTURE_SECRET'],capture_output=True,text=True,timeout=10)
    assert child.returncode==2 and child.stderr=='PAPER_ACCOUNT_EVIDENCE_USAGE_ERROR\n' and not child.stdout


def test_public_fixture_omits_account_balances_and_original_ids():
    r=subject.fixture_report();assert r['passed']
    assert all(r[k] is False for k in subject.FLAGS)
    assert all(token not in json.dumps(r) for token in ('01234567-89ab-cdef-0123-456789abcdef','nx_accept_fixture','802','1000'))


@pytest.mark.parametrize('field,code',[('pacing_head_sha256','COMPONENT_HEAD_DISAGREEMENT'),('source_attested','COMPONENT_AUTHORITY_DISAGREEMENT')])
def test_missing_component_binding_or_false_authority_fails_closed(tmp_path,monkeypatch,field,code):
    args=options(tmp_path);original=subject.requests.review
    def omitted(**kw):
        r=original(**kw);r.pop(field);return r
    monkeypatch.setattr(subject.requests,'review',omitted)
    with pytest.raises(ValueError,match=code):subject.review(**args)
