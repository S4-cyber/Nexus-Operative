"""Private activity acceptance composition with injected vault/HTTPS only."""
import json
import os
import sys

import pytest

from nexus import paper_activity_acceptance_probe as probe
from nexus import paper_credentials as credentials
from nexus.broker_activity_store import BrokerActivityStore
from nexus.paper_read_pacing import PaperReadPacing
from test_alpaca_activity_collection import START, END, NOW, fee
from test_alpaca_activity_normalizer import inputs as fill_inputs
from test_alpaca_paper_inspection import Opener, account, ACCOUNT, OTHER, KEY, SECRET
from test_paper_credentials import save, Vault


@pytest.fixture
def vault(monkeypatch):
    store=Vault()
    monkeypatch.setattr(credentials,'_vault',lambda:store)
    monkeypatch.setattr(credentials,'_epoch',0)
    monkeypatch.setattr(credentials,'_blocked',False)
    return store


def paths(tmp_path, orders=None):
    source = tmp_path/'private'/'orders.json';source.parent.mkdir()
    source.write_text(json.dumps([] if orders is None else orders),encoding='utf-8')
    return source,tmp_path/'private'/'pacing.sqlite3',tmp_path/'private'/'activity.sqlite3'


def run(tmp_path, opener=None, orders=None, at=NOW, **changes):
    source,pacing,activity=paths(tmp_path,orders)
    args=dict(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
        orders_path=source,after=START,until=END,confirm_read_only_paper=True,
        opener=opener or Opener([account(),[fee()],account()]),wall_clock=lambda:at,elapsed_clock=lambda:50)
    args.update(changes)
    return probe.probe(**args),source,pacing,activity


def test_saved_pair_read_binds_pacing_collection_and_private_retention(vault,tmp_path):
    save();opener=Opener([account(),[fee()],account()]);result,source,pacing,activity=run(tmp_path,opener)
    assert len(opener.requests)==3 and result['status']=='REVIEW_REQUIRED' and result['read_completed']
    assert result['collection']['counts']==dict(order_snapshots=0,rows_received=1,
        normalized_activities=1,executions=0,fee_debits=1)
    assert result['collection']['recognized_fee_debits']=={'BTC':'0','USD':'0.01'}
    assert result['local_retention']['retained_batches']==1
    assert result['local_retention']['retained_collection_observations']==1
    assert result['local_pacing']['sessions']==1 and result['local_pacing']['events']==2
    assert all(result[field] is False for field in probe.FLAGS)
    from nexus.alpaca_paper_contract import sha
    assert result['acceptance_record_sha256']==sha(result['acceptance_record_scope'])
    encoded=json.dumps(result)
    assert all(value not in encoded for value in (KEY,SECRET,fee()['id'],str(source),str(pacing),str(activity)))
    assert result['raw_activity_rows_included'] is result['raw_order_identities_included'] is False
    assert PaperReadPacing(pacing,ACCOUNT).inspect()['last_outcome']=='SUCCESS'
    assert BrokerActivityStore(activity,ACCOUNT).inspect()['retained_activities']==1


def test_fill_and_order_identities_remain_private_while_counts_and_digest_are_bound(vault,tmp_path):
    save();rows,orders=fill_inputs();row=rows[0]
    opener=Opener([account(),[row],account()]);result,source,_,activity=run(tmp_path,opener,orders)
    assert result['collection']['counts']['order_snapshots']==2
    assert result['collection']['counts']['executions']==1
    encoded=json.dumps(result)
    private=(row['id'],row['order_id'],orders[0]['client_order_id'],orders[1]['client_order_id'])
    assert all(value not in encoded for value in private)
    retained=BrokerActivityStore(activity,ACCOUNT).inspect()
    assert retained['retained_activities']==1 and retained['retained_batches']==1
    assert all(value in json.dumps(retained) for value in private[:3])


def test_two_full_pages_report_bounded_continuation_without_exposing_cursor(vault,tmp_path):
    from test_alpaca_activity_collection import page
    save();first,second=page(),page(101)
    result,_,_,_=run(tmp_path,Opener([account(),first,second,account()]))
    assert result['collection']['page_counts']==[100,100]
    assert result['collection']['collection_limit_reached'] and result['collection']['continuation_required']
    encoded=json.dumps(result)
    assert first[-1]['id'] not in encoded and second[-1]['id'] not in encoded


def test_confirmation_paths_and_order_file_fail_before_vault_or_network(vault,tmp_path):
    source,pacing,activity=paths(tmp_path);opener=Opener()
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
            orders_path=source,after=START,until=END,confirm_read_only_paper=False,opener=opener)
    assert vault.reads==0 and not opener.requests and not pacing.exists() and not activity.exists()
    public=probe.ROOT/'private-orders.json'
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
            orders_path=public,after=START,until=END,confirm_read_only_paper=True,opener=opener)
    assert vault.reads==0 and not opener.requests


@pytest.mark.parametrize('kind',['duplicate-json','symlink','hardlink','directory'])
def test_unsafe_order_input_is_rejected_before_vault_or_network(vault,tmp_path,kind):
    source,pacing,activity=paths(tmp_path);opener=Opener()
    if kind=='duplicate-json':source.write_text('[{"a":1,"a":2}]',encoding='utf-8')
    elif kind=='symlink':
        if os.name=='nt':pytest.skip('No Windows symlink privilege assumed')
        target=tmp_path/'target.json';target.write_text('[]',encoding='utf-8');source.unlink();source.symlink_to(target)
    elif kind=='hardlink':
        alias=tmp_path/'alias.json';os.link(source,alias)
    else:source.unlink();source.mkdir()
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
            orders_path=source,after=START,until=END,confirm_read_only_paper=True,opener=opener)
    assert vault.reads==0 and not opener.requests


def test_cooldown_foreign_history_and_store_cap_block_before_provider(vault,tmp_path,monkeypatch):
    save();result,source,pacing,activity=run(tmp_path)
    before_pacing=pacing.read_bytes();before_activity=activity.read_bytes();opener=Opener()
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
            orders_path=source,after=START,until=END,confirm_read_only_paper=True,
            opener=opener,wall_clock=lambda:NOW+59,elapsed_clock=lambda:50)
    assert not opener.requests and pacing.read_bytes()==before_pacing and activity.read_bytes()==before_activity
    foreign=tmp_path/'foreign.sqlite3';PaperReadPacing(foreign,OTHER).begin(admission=lambda:NOW)
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=foreign,activity_store_path=tmp_path/'fresh.db',
            orders_path=source,after=START,until=END,confirm_read_only_paper=True,opener=opener)
    assert not opener.requests
    monkeypatch.setattr('nexus.broker_activity_store.MAX_BATCHES',1)
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
            orders_path=source,after=START,until=END,confirm_read_only_paper=True,
            opener=opener,wall_clock=lambda:NOW+60,elapsed_clock=lambda:50)
    assert not opener.requests


def test_status_replays_both_journals_without_order_vault_or_network(vault,tmp_path):
    save();_,source,pacing,activity=run(tmp_path)
    before=(pacing.read_bytes(),activity.read_bytes());reads=vault.reads
    source.unlink()
    result=probe.status(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity)
    assert result['read_only'] and result['retained_batches']==1 and result['retained_activities']==1
    assert not result['pending_read'] and result['session_capacity_remaining']>0
    assert vault.reads==reads and before==(pacing.read_bytes(),activity.read_bytes())


def test_lost_final_credential_confirmation_emits_no_report_but_retains_reviewable_state(vault,tmp_path,monkeypatch):
    save();source,pacing,activity=paths(tmp_path);opener=Opener([account(),[fee()],account()])
    original=credentials._capture;calls=[]
    def changed():
        value=original();calls.append(1)
        if len(calls)==6:raise credentials.PaperVaultError('private lost readback')
        return value
    monkeypatch.setattr(credentials,'_capture',changed)
    with pytest.raises(ValueError):
        probe.probe(expected_account_id=ACCOUNT,pacing_path=pacing,activity_store_path=activity,
            orders_path=source,after=START,until=END,confirm_read_only_paper=True,
            opener=opener,wall_clock=lambda:NOW,elapsed_clock=lambda:50)
    assert PaperReadPacing(pacing,ACCOUNT).inspect()['last_outcome']=='SUCCESS'
    assert BrokerActivityStore(activity,ACCOUNT).inspect()['retained_collection_observations']==1


def test_cli_errors_are_fixed_and_status_does_not_require_private_order_file(vault,tmp_path,monkeypatch,capsys):
    source,pacing,activity=paths(tmp_path);source.unlink()
    monkeypatch.setattr(sys,'argv',['tool','--expected-account',ACCOUNT,'--pacing',str(pacing),
        '--activity-store',str(activity),'--status-only'])
    assert probe.main()==0
    data=json.loads(capsys.readouterr().out);assert data['mode']=='PAPER_ACTIVITY_ACCEPTANCE_STATUS'
    monkeypatch.setattr(sys,'argv',['tool','--api-secret','private-mistake'])
    with pytest.raises(SystemExit) as stopped:probe.main()
    assert stopped.value.code==2 and capsys.readouterr().err.strip()=='PAPER_ACTIVITY_ACCEPTANCE_PROBE_USAGE_ERROR'
