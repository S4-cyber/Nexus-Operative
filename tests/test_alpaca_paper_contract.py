import copy
import json
from datetime import datetime,timezone,timedelta
import pytest
from nexus import alpaca_paper_contract as contract

ASSET=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,
           min_order_size='0.5',min_trade_increment='0.5',price_increment='0.5')


def plan(side='BUY',**kwargs):
    data=dict(client_order_id='nx_fixture',product='BTC-USD',side=side,quantity='2',limit_price='100',
              asset=ASSET,metadata_at=1000,evaluated_at=1001)
    data.update(kwargs);return contract.preview(**data)


def response(index=0,**changes):
    data=dict(id='01234567-89ab-cdef-0123-456789abcdef',client_order_id='nx_fixture',symbol='BTC/USD',side='buy',
              type='limit',time_in_force='ioc',asset_class='crypto',qty='2',limit_price='100',
              status='new',filled_qty='0',filled_avg_price=None,
              updated_at=(datetime(2026,10,17,18,tzinfo=timezone.utc)+timedelta(seconds=index)).isoformat())
    data.update(changes);return data


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_preview_is_disconnected_quantity_ioc_limit_and_numeric_replay_stable(side):
    first=plan(side);second=plan(side,quantity='2.000',limit_price='100.0')
    assert first['payload']==second['payload'] and first['payload_sha256']==second['payload_sha256']
    assert first['payload']['side']==side.lower() and first['payload']['time_in_force']=='ioc'
    assert first['payload']['type']=='limit' and 'notional' not in first['payload']
    assert first['hypothetical_url']=='https://paper-api.alpaca.markets/v2/orders'
    assert not first['sendable'] and not first['broker_connected'] and not first['ledger_applied']
    assert first['blockers'] and first['reference_notional']=='200'


@pytest.mark.parametrize('changes',[
    {'client_order_id':'bad/id'},{'client_order_id':'ñ'},{'product':'ETH-USD'},{'side':'SHORT'},
    {'quantity':'.1'},{'quantity':'.7'},{'limit_price':'100.1'},{'quantity':'2.5'},
    {'metadata_at':900},{'metadata_at':1002},{'quantity':True},{'limit_price':'NaN'},
    {'quantity':'1e-99999'},{'asset':{**ASSET,'tradable':False}},
    {'asset':{**ASSET,'symbol':'ETH/USD'}},{'asset':{**ASSET,'min_trade_increment':'0'}}])
def test_invalid_or_unverified_request_fails_without_mutation(changes):
    before=copy.deepcopy(ASSET)
    with pytest.raises(ValueError):plan(**changes)
    assert ASSET==before


def test_partial_pending_cancel_then_cancelled_never_infers_zero_holdings_or_fees():
    tracker=contract.SnapshotTracker(plan())
    assert tracker.observe(response())['observed_quantity_increase']=='0'
    partial=response(1,status='partially_filled',filled_qty='1',filled_avg_price='99')
    assert tracker.observe(partial)['observed_quantity_increase']=='1'
    assert tracker.observe(partial)['duplicate']
    pending=tracker.observe(response(2,status='pending_cancel',filled_qty='1',filled_avg_price='99'))
    assert not pending['snapshot']['terminal'] and not pending['snapshot']['cancellation_confirmed']
    canceled=tracker.observe(response(3,status='canceled',filled_qty='1',filled_avg_price='99'))
    assert canceled['snapshot']['terminal'] and canceled['snapshot']['cancellation_confirmed']
    assert canceled['snapshot']['filled_quantity']=='1' and canceled['observed_quantity_increase']=='0'
    assert not canceled['snapshot']['fee_reconciled'] and not canceled['snapshot']['position_reconciled']
    assert not canceled['ledger_applied']
    canceled['snapshot']['filled_quantity']='0'
    assert tracker.status()['latest']['filled_quantity']=='1'


def test_fill_wins_pending_cancel_race_but_terminal_rewrite_requires_review():
    tracker=contract.SnapshotTracker(plan())
    tracker.observe(response(status='pending_cancel',filled_qty='1',filled_avg_price='99'))
    filled=tracker.observe(response(1,status='filled',filled_qty='2',filled_avg_price='99.5'))
    assert filled['snapshot']['terminal'] and filled['observed_quantity_increase']=='1'
    before=tracker.status()
    with pytest.raises(ValueError,match='BROKER_TERMINAL_STATE_CHANGED'):
        tracker.observe(response(2,status='canceled',filled_qty='2',filled_avg_price='99.5'))
    assert tracker.status()==before


@pytest.mark.parametrize('changes',[
    {'id':'not-uuid'},{'client_order_id':'other'},{'side':'sell'},{'symbol':'ETH/USD'},
    {'symbol':[]},{'asset_class':'us_equity'},{'qty':'3'},{'limit_price':'99'},
    {'status':'pending_replace'},{'status':[]},{'filled_qty':'3'},{'filled_qty':'-1'},
    {'filled_qty':'1','filled_avg_price':None},{'filled_qty':'1','filled_avg_price':'101'},
    {'status':'filled','filled_qty':'1','filled_avg_price':'99'},
    {'status':'partially_filled','filled_qty':'0'}, {'status':'rejected','filled_qty':'1','filled_avg_price':'99'},
    {'updated_at':'2026-10-17T18:00:00'},{'updated_at':None}])
def test_bad_observation_cannot_mutate_tracker(changes):
    tracker=contract.SnapshotTracker(plan());before=tracker.status()
    with pytest.raises(ValueError):tracker.observe(response(**changes))
    assert tracker.status()==before


def test_prior_observation_and_conflicting_cumulative_updates_are_not_reapplied():
    tracker=contract.SnapshotTracker(plan());first=response();tracker.observe(first)
    tracker.observe(response(2,status='partially_filled',filled_qty='1',filled_avg_price='99'))
    before=tracker.status()
    for item in (first,response(2,status='pending_cancel',filled_qty='1',filled_avg_price='99'),
                 response(3,status='partially_filled',filled_qty='.5',filled_avg_price='99'),
                 response(3,status='partially_filled',filled_qty='1',filled_avg_price='98')):
        with pytest.raises(ValueError):tracker.observe(item)
        assert tracker.status()==before


def test_cap_preserves_current_duplicate_without_silent_eviction(monkeypatch):
    monkeypatch.setattr(contract,'MAX_SNAPSHOTS',2)
    tracker=contract.SnapshotTracker(plan());tracker.observe(response())
    current=response(1,status='pending_cancel');tracker.observe(current)
    assert tracker.observe(current)['duplicate']
    before=tracker.status()
    with pytest.raises(ValueError,match='RETENTION_CAP'):tracker.observe(response(2,status='canceled'))
    assert tracker.status()==before


def test_extra_response_fields_never_enter_retained_snapshot():
    tracker=contract.SnapshotTracker(plan());result=tracker.observe(response(api_key='private-fixture',account_number='private-account'))
    assert 'private' not in json.dumps(result) and 'private' not in json.dumps(tracker.status())
    assert contract.normalize(response(symbol='BTCUSD'),plan()['payload'])['symbol']=='BTC/USD'


def test_contract_api_read_only_and_original_external_submission_still_denied(tmp_path,monkeypatch):
    import nexus.app as backend
    from fastapi.testclient import TestClient
    from nexus.durable_orders import DurableOrderGateway
    gateway=DurableOrderGateway(tmp_path/'orders.sqlite3')
    monkeypatch.setattr(backend,'research_order_gateway',gateway)
    client=TestClient(backend.app);report=client.get('/api/execution/alpaca-paper/contract').json()
    assert report['read_only'] and not report['external_submission_supported']
    assert not report['pending_cancel_is_terminal'] and not report['fee_reconciled']
    assert client.post('/api/execution/alpaca-paper/contract',json={}).status_code==405
    assert client.get('/api/execution/connectors').json()['alpaca_paper_contract']==report
    assert not gateway.path.exists()
    with pytest.raises(ValueError,match='EXTERNAL_BROKER_NOT_AUTHORIZED'):
        gateway.submit('blocked','BTC-USD','BUY','25',provider='alpaca_paper')


def test_sell_limit_and_broker_identity_changes_fail_closed():
    tracker=contract.SnapshotTracker(plan('SELL'))
    tracker.observe(response(side='sell',status='pending_cancel',filled_qty='1',filled_avg_price='101'))
    before=tracker.status()
    with pytest.raises(ValueError,match='LIMIT_DISAGREEMENT'):
        tracker.observe(response(1,side='sell',status='filled',filled_qty='2',filled_avg_price='99'))
    with pytest.raises(ValueError,match='ASSIGNED_ID_CHANGED'):
        tracker.observe(response(1,id='11234567-89ab-cdef-0123-456789abcdef',side='sell',status='filled',filled_qty='2',filled_avg_price='101'))
    assert tracker.status()==before


def test_plan_digest_and_mutable_copies_do_not_change_tracker_terms():
    original=plan();damaged=copy.deepcopy(original);damaged['payload']['qty']='1'
    with pytest.raises(ValueError,match='INVALID_BROKER_PREVIEW'):contract.SnapshotTracker(damaged)
    tracker=contract.SnapshotTracker(original);original['payload']['qty']='1'
    assert tracker.observe(response())['snapshot']['filled_quantity']=='0'


def test_repeatable_fixture_and_cli_create_no_account_files(tmp_path,monkeypatch):
    import os,subprocess,sys
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path));before=dict(os.environ)
    report=contract.fixture_report()
    assert report['passed'] and report['current_replay_duplicate']
    assert report['steps'][2]['snapshot']['terminal'] is False
    assert report['steps'][3]['snapshot']['filled_quantity']=='1'
    assert report['steps'][3]['observed_quantity_increase']=='0'
    assert not report['ledger_applied'] and not report['fee_reconciled']
    child=subprocess.run([sys.executable,'-m','nexus.alpaca_paper_contract'],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)==report
    assert not list(tmp_path.iterdir()) and dict(os.environ)==before


def test_admission_progression_and_status_regression_checks():
    tracker=contract.SnapshotTracker(plan())
    for index,status in enumerate(('accepted','pending_new','new')):
        tracker.observe(response(index,status=status))
    before=tracker.status()
    with pytest.raises(ValueError,match='STATUS_TRANSITION_REQUIRES_REVIEW'):
        tracker.observe(response(3,status='accepted'))
    assert tracker.status()==before


def test_precision_and_step_validation_never_round_a_request_into_acceptance():
    asset={**ASSET,'min_trade_increment':'0.000000000000000001','price_increment':'0.000000000000000001'}
    result=plan(quantity='1.000000000000000001',limit_price='100',asset=asset)
    assert result['payload']['qty']=='1.000000000000000001'
    assert result['reference_notional']=='100.0000000000000001'
    with pytest.raises(ValueError,match='LOCAL_NOTIONAL_CAP'):
        plan(quantity='2.000000000000000001',limit_price='100',asset=asset)


def test_equivalent_zero_observations_are_idempotent():
    tracker=contract.SnapshotTracker(plan())
    tracker.observe(response(filled_qty='0'))
    replay=tracker.observe(response(filled_qty='-0.000'))
    assert replay['duplicate'] and replay['snapshot']['filled_quantity']=='0'
    assert replay['observed_quantity_increase']=='0'
