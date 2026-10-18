"""Exact-head retained fills, private CLI and conservative benchmark/fee scope."""
import json
import subprocess
import sys

import pytest
from nexus import paper_execution_quality_review as subject
from nexus.alpaca_paper_contract import preview,canonical
from nexus.broker_request_journal import BrokerRequestJournal
from test_paper_request_activity_acceptance import prepared


def snapshot(root):return tuple((p.name,p.read_bytes()) for p in sorted(root.iterdir()) if p.is_file())


def test_derives_original_terms_and_all_explicit_prices_without_writes(tmp_path):
    args=subject._fixture(tmp_path);before=snapshot(tmp_path)
    r=subject.review(**args);q=r['execution_quality']
    assert q['unique_fills']==2 and q['filled_quantity']=='2' and q['fill_vwap']=='99'
    assert q['signed_gross_shortfall_usd']=='-2' and q['signed_shortfall_with_supplied_fees_usd'] is None
    assert q['arrival_to_first_supplied_fill_ns']==1_000_000_000
    assert q['arrival_to_last_supplied_fill_ns']==2_000_000_000 and q['decision_to_arrival_ns']==1
    assert r['all_retained_original_fills_included'] and r['journals_rechecked_unchanged']
    assert r['payload_sha256'] and r['request_head_sha256']==args['expected_request_head']
    assert all(r[k] is False for k in subject.FLAGS) and all(q[k] is False for k in subject.quality.FLAGS)
    assert subject.review(**args)==r and snapshot(tmp_path)==before


def test_cannot_select_only_favorable_fill_by_shortening_benchmark_interval(tmp_path):
    args=subject._fixture(tmp_path);args['benchmark']['end_at_ns']=args['benchmark']['arrival_at_ns']+1_500_000_000
    before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='FILL_TIME_ORDER'):subject.review(**args)
    assert snapshot(tmp_path)==before


@pytest.mark.parametrize('field',['expected_request_head','expected_activity_head'])
def test_stale_heads_refuse_without_rewriting_history(tmp_path,field):
    args=subject._fixture(tmp_path);args[field]='0'*64;before=snapshot(tmp_path)
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT'):subject.review(**args)
    assert snapshot(tmp_path)==before


def test_unknown_or_foreign_request_and_foreign_account_do_not_produce_cost_report(tmp_path):
    args=subject._fixture(tmp_path)
    for changed in (dict(args,client_order_id='unknown_original'),dict(args,expected_account_id='11234567-89ab-cdef-0123-456789abcdef')):
        with pytest.raises(ValueError):subject.review(**changed)


def test_individual_limit_violation_blocks_even_when_average_price_matches(tmp_path):
    args,_,_=prepared(tmp_path,prices=('85','113'))
    epoch=subject._instant_ns('2026-10-17T18:00:00Z')
    args.update(client_order_id='nx_accept_fixture',benchmark=dict(decision_mid='100',decision_at_ns=epoch-1,
        arrival_bid='99',arrival_ask='101',arrival_at_ns=epoch,end_at_ns=epoch+3_000_000_000,unfilled_mark=None))
    with pytest.raises(ValueError,match='ACCEPTED_REQUEST_AGREEMENT_REQUIRED'):subject.review(**args)


def test_request_append_during_arithmetic_is_retained_and_review_fails_without_retry(tmp_path,monkeypatch):
    args=subject._fixture(tmp_path);original=subject.quality.review;calls=[]
    def changed(**kw):
        r=original(**kw)
        if kw['fills']:
            calls.append(1)
            asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
                min_order_size='.5',min_trade_increment='.5',price_increment='.5')
            BrokerRequestJournal(args['request_journal_path'],args['expected_account_id']).prepare(
                preview('later_request','BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001))
        return r
    monkeypatch.setattr(subject.quality,'review',changed)
    with pytest.raises(ValueError,match='HEAD_DISAGREEMENT|JOURNALS_CHANGED'):subject.review(**args)
    assert calls==[1]
    assert len(BrokerRequestJournal(args['request_journal_path'],args['expected_account_id']).recovery()['requests'])==2


@pytest.mark.parametrize('field',['ready_for_routing','fee_coverage_complete','source_attested'])
def test_promoted_accepted_component_is_rejected(tmp_path,monkeypatch,field):
    args=subject._fixture(tmp_path);original=subject.accepted.review
    def promoted(**kw):
        r=original(**kw);r[field]=True;return r
    monkeypatch.setattr(subject.accepted,'review',promoted)
    with pytest.raises(ValueError,match='ACCEPTED_REQUEST_AGREEMENT_REQUIRED'):subject.review(**args)


@pytest.mark.parametrize('field,value',[('ready_for_routing',True),('actual_fee_coverage_verified',True),
    ('signed_shortfall_with_supplied_fees_usd','0'),('all_supplied_fees_explicit',True),('unique_fills',1)])
def test_quality_component_cannot_drop_fills_invent_fees_or_promote_authority(tmp_path,monkeypatch,field,value):
    args=subject._fixture(tmp_path);original=subject.quality.review
    def changed(**kw):
        r=original(**kw)
        if kw['fills']:r[field]=value
        return r
    monkeypatch.setattr(subject.quality,'review',changed)
    with pytest.raises(ValueError,match='QUALITY_COMPONENT_DISAGREEMENT'):subject.review(**args)


def test_missing_journals_and_aliased_paths_never_initialize_storage(tmp_path):
    (tmp_path/'fixture').mkdir();args=subject._fixture(tmp_path/'fixture')
    # The fixture intentionally lives elsewhere; the selected missing paths stay absent.
    root=tmp_path/'missing'
    for name in ('request_journal_path','pacing_path','activity_store_path','acceptance_store_path'):args[name]=root/(name+'.sqlite3')
    with pytest.raises(ValueError):subject.review(**args)
    assert not root.exists()
    args['pacing_path']=args['request_journal_path']
    with pytest.raises(ValueError,match='DISTINCT_JOURNALS_REQUIRED'):subject.review(**args)
    assert not root.exists()


@pytest.mark.parametrize('value',[{'private':'FIXTURE'},None,{'fills':[]}])
def test_malformed_benchmark_refuses_before_journal_access(tmp_path,monkeypatch,value):
    args=subject._fixture(tmp_path);args['benchmark']=value
    monkeypatch.setattr(subject.accepted,'review',lambda **kw:pytest.fail('journal read'))
    with pytest.raises(ValueError,match='BENCHMARK_SHAPE'):subject.review(**args)


def test_rfc3339_nanoseconds_are_exact_and_offset_normalized():
    first=subject._instant_ns('2026-10-17T18:00:00.123456788Z')
    second=subject._instant_ns('2026-10-17T11:00:00.123456789-07:00')
    assert second-first==1 and first>2**53


def command(args,path):
    result=[]
    for name,key in (('expected-account','expected_account_id'),('request-journal','request_journal_path'),
        ('pacing','pacing_path'),('activity-store','activity_store_path'),('acceptance-store','acceptance_store_path'),
        ('request-head','expected_request_head'),('activity-head','expected_activity_head'),('client-order-id','client_order_id')):
        result+=['--'+name,str(args[key])]
    return [*result,'--benchmark',str(path)]


def test_actual_private_cli_denies_network_vault_and_preserves_all_files(tmp_path):
    args=subject._fixture(tmp_path);path=tmp_path/'benchmark.json';path.write_text(canonical(args['benchmark']))
    script='''
import sys
from nexus import paper_credentials
from nexus.paper_execution_quality_review import main
def denied():raise RuntimeError('VAULT_DENIED')
def audit(event,args):
 if event in ('socket.connect','socket.bind'):raise RuntimeError('NETWORK_DENIED')
paper_credentials._vault=denied;sys.addaudithook(audit)
sys.argv=['fixture']+sys.argv[1:]
raise SystemExit(main())
'''
    before=snapshot(tmp_path)
    child=subprocess.run([sys.executable,'-c',script,*command(args,path)],capture_output=True,text=True,timeout=20)
    assert child.returncode==0 and not child.stderr
    assert json.loads(child.stdout)==subject.review(**args) and snapshot(tmp_path)==before


def test_benchmark_file_changed_during_review_is_not_acknowledged(tmp_path,monkeypatch,capsys):
    args=subject._fixture(tmp_path);path=tmp_path/'benchmark.json';path.write_text(canonical(args['benchmark']))
    original=subject.review
    def changed(**kw):
        r=original(**kw);path.write_text(path.read_text()+'\n');return r
    monkeypatch.setattr(subject,'review',changed);monkeypatch.setattr(sys,'argv',['fixture',*command(args,path)])
    assert subject.main()==2 and capsys.readouterr().out=='PAPER_EXECUTION_QUALITY_FAILED\n'


@pytest.mark.parametrize('raw',[b'{"decision_mid":"100","decision_mid":"101"}',b'{"value":NaN}',b'x'*4097])
def test_private_benchmark_parser_rejects_duplicate_nonfinite_or_oversized_input(tmp_path,raw):
    path=tmp_path/'benchmark.json';path.write_bytes(raw)
    with pytest.raises(ValueError):subject._load_benchmark(path)


def test_cli_usage_never_echoes_mistaken_secret_argument(tmp_path):
    args=subject._fixture(tmp_path);path=tmp_path/'benchmark.json';path.write_text('PRIVATE_FIXTURE')
    child=subprocess.run([sys.executable,'-m','nexus.paper_execution_quality_review',*command(args,path),
        '--secret','PRIVATE_SECRET_FIXTURE'],capture_output=True,text=True,timeout=10)
    assert child.returncode==2 and child.stderr=='PAPER_EXECUTION_QUALITY_USAGE_ERROR\n' and not child.stdout


def test_public_synthetic_fixture_omits_private_ids_prices_paths_and_hashes():
    result=subject.fixture_report();assert result['passed'] and all(result[k] is True for k in subject.CHECKS)
    assert all(result[k] is False for k in subject.FLAGS)
    encoded=json.dumps(result)
    assert all(token not in encoded for token in ('nx_accept_fixture','01234567','benchmark_sha256','fill_vwap','1792195200'))


@pytest.mark.parametrize('field,value,code',[
    ('all_original_fills_derived',False,'PAPER_EXECUTION_QUALITY'),
    ('missing_fees_unlinked',False,'PAPER_EXECUTION_QUALITY'),
    ('benchmark_source_verified',True,'PAPER_EXECUTION_QUALITY_AUTHORITY'),
    ('ready_for_routing',True,'PAPER_EXECUTION_QUALITY_AUTHORITY')])
def test_release_profile_rejects_missing_bindings_or_promoted_paper_quality(field,value,code):
    from nexus import release_trials
    r=subject.fixture_report();r[field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):
        release_trials._validate('capacity',dict(execution_quality=subject.quality.fixture_report(),paper_execution_quality=r))
