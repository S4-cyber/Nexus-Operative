"""Local provider-scope paper-read budget and conservative composition."""
import sqlite3

import pytest

from nexus import alpaca_paper_inspection as reader
from nexus.paper_provider_read_budget import PaperProviderReadBudget,ZERO
from nexus.paper_read_pacing import PaperReadPacing
from test_alpaca_paper_inspection import ACCOUNT,OTHER,KEY,SECRET,Opener,account


def complete(budget,account_id=ACCOUNT,operation='BTC_ASSET_RULES',at=1000,before=ZERO,after='a'*64,gets=3):
    with budget.ownership() as owner:
        token=budget.begin(account_id=account_id,account_before_head_sha256=before,
            operation=operation,admission=lambda:at,ownership=owner)
        return budget.finish(token,account_after_head_sha256=after,actual_gets=gets,
            admission=lambda:at+1,ownership=owner,status_code=200)


def test_missing_inspection_is_read_only_and_authority_false(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'missing'/'provider.sqlite3')
    state=budget.inspect()
    assert state['head_sha256']==ZERO and state['sessions']==0
    assert not budget.path.parent.exists()
    assert not state['broker_wide_quota_verified'] and not state['ready_for_routing']


def test_completed_sessions_bind_two_accounts_without_plain_identifiers(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    first=complete(budget)
    second=complete(budget,OTHER,operation='ACCOUNT_INVENTORY',at=1061,before='b'*64,after='c'*64,gets=4)
    state=budget.inspect();history=budget.history();content=budget.path.read_bytes()
    assert state['head_sha256']==second and state['completed_sessions']==2 and state['completed_account_count']==2
    assert history[0]['after_head_sha256']==first and history[1]['account_before_head_sha256']=='b'*64
    assert ACCOUNT.encode() not in content and OTHER.encode() not in content


def test_cooldown_and_stale_head_do_not_append(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget);before=budget.path.read_bytes()
    with pytest.raises(ValueError,match='COOLDOWN'):
        budget.preflight(admission=lambda:1059)
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        budget.preflight(admission=lambda:1061,expected_head=ZERO)
    assert budget.path.read_bytes()==before


def test_process_unknown_can_only_be_explicitly_interrupted(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    with budget.ownership() as owner:
        token=budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
            operation='BTC_QUOTE_OBSERVATION',admission=lambda:1000,ownership=owner)
    state=budget.inspect();before=budget.path.read_bytes()
    with pytest.raises(ValueError,match='PENDING_REVIEW'):
        budget.preflight(admission=lambda:2000)
    assert budget.path.read_bytes()==before
    result=budget.interrupt(token,expected_head=state['head_sha256'],confirm_interrupted=True,admission=lambda:2000)
    after=budget.inspect()
    assert result['interruption_recorded'] and after['last_outcome']=='INTERRUPTED_UNKNOWN'
    assert after['next_allowed_at']==88400 and budget.history()[-1]['account_after_head_sha256'] is None


def test_tampered_chain_and_foreign_provider_are_rejected(tmp_path):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path);complete(budget)
    with pytest.raises(ValueError,match='PROVIDER_OR_SCHEMA'):
        PaperProviderReadBudget(path,'OTHER_PAPER').inspect()
    with sqlite3.connect(path) as db:db.execute("UPDATE events SET previous=? WHERE seq=1",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        budget.inspect()


def test_related_account_and_provider_paths_fail_before_creation(tmp_path):
    pacing=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    for path in (pacing.path,pacing.path.with_name(pacing.path.name+'-wal'),pacing.path.with_name(pacing.path.name+'.owner.lock')):
        budget=PaperProviderReadBudget(path);opener=Opener([account(),[],[],account()])
        with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
            reader.inspect_account(KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
                pacing=pacing,provider_budget=budget,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50)
        assert not opener.requests and not pacing.path.exists()


def test_known_account_block_never_creates_provider_unknown(tmp_path):
    pacing=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    token=pacing.begin(admission=lambda:1000);pacing.finish(token,admission=lambda:1000,status_code=200)
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');opener=Opener()
    with pytest.raises(reader.PaperInspectionError,match='RESPONSE_INVALID'):
        reader.inspect_account(KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            pacing=pacing,provider_budget=budget,opener=opener,wall_clock=lambda:1059,elapsed_clock=lambda:50)
    assert not opener.requests and not budget.path.exists()


def test_provider_finish_failure_preserves_account_result_and_provider_unknown(tmp_path,monkeypatch):
    pacing=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');original=budget.finish
    def fail(*args,**kwargs):raise ValueError('injected')
    monkeypatch.setattr(budget,'finish',fail)
    opener=Opener([account(),[],[],account()])
    with pytest.raises(reader.PaperInspectionError,match='RESPONSE_INVALID'):
        reader.inspect_account(KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            pacing=pacing,provider_budget=budget,opener=opener,wall_clock=lambda:1000,elapsed_clock=lambda:50)
    assert pacing.inspect()['last_outcome']=='SUCCESS'
    assert budget.inspect()['pending_token'] is not None and budget.inspect()['completed_sessions']==0
    monkeypatch.setattr(budget,'finish',original)


def test_expected_provider_head_blocks_before_get_or_account_begin(tmp_path):
    pacing=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT);budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    complete(budget,operation='ACCOUNT_INVENTORY',gets=4);opener=Opener()
    with pytest.raises(reader.PaperInspectionError,match='RESPONSE_INVALID'):
        reader.inspect_account(KEY,SECRET,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
            pacing=pacing,provider_budget=budget,expected_provider_budget_head=ZERO,
            opener=opener,wall_clock=lambda:1061,elapsed_clock=lambda:50)
    assert not opener.requests and not pacing.path.exists()
