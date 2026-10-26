"""Local provider-scope paper-read budget and conservative composition."""
import sqlite3

import pytest

from nexus import alpaca_paper_inspection as reader
from nexus import paper_provider_read_budget as subject
from nexus.paper_provider_read_budget import PaperProviderReadBudget,ZERO
from nexus.paper_read_pacing import PaperReadPacing
from test_alpaca_paper_inspection import ACCOUNT,OTHER,KEY,SECRET,Opener,account


def complete(budget,account_id=ACCOUNT,operation='BTC_ASSET_RULES',at=1000,before=ZERO,
             after='a'*64,gets=3,status_code=200):
    with budget.ownership() as owner:
        token=budget.begin(account_id=account_id,account_before_head_sha256=before,
            operation=operation,admission=lambda:at,ownership=owner)
        return budget.finish(token,account_after_head_sha256=after,actual_gets=gets,
            admission=lambda:at+1,ownership=owner,status_code=status_code)


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
    assert after['review_required'] and after['recoveries']==0


@pytest.mark.parametrize('status_code,outcome',[(401,'AUTH_DENIED'),(302,'REVIEW_REQUIRED')])
def test_exact_reviewed_recovery_clears_only_the_block_and_preserves_cooldown(
        tmp_path,status_code,outcome):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    complete(budget,status_code=status_code)
    blocked=budget.inspect();history=budget.history();before_next=blocked['next_allowed_at']
    assert blocked['review_required'] and blocked['last_outcome']==outcome
    with budget.ownership() as owner:
        result=budget.recover_review_block(expected_head=blocked['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
    recovered=budget.inspect()
    assert result['blocked_outcome']==outcome and result['review_block_cleared']
    assert result['cooldown_shortened'] is False and result['uncertainty_preserved']
    assert recovered['review_required'] is False and recovered['recoveries']==1
    assert recovered['next_allowed_at']==before_next and recovered['sessions']==1
    assert recovered['events']==3 and budget.history()==history
    assert ACCOUNT.encode() not in budget.path.read_bytes()
    with pytest.raises(ValueError,match='COOLDOWN'):
        budget.preflight(admission=lambda:before_next-1)
    assert budget.preflight(admission=lambda:before_next)==recovered['head_sha256']


def test_interrupted_unknown_requires_explicit_review_even_after_full_cooldown(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    with budget.ownership() as owner:
        token=budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
            operation='BTC_QUOTE_OBSERVATION',admission=lambda:1000,ownership=owner)
    pending=budget.inspect()
    budget.interrupt(token,expected_head=pending['head_sha256'],confirm_interrupted=True,
        admission=lambda:2000)
    interrupted=budget.inspect();saved_history=budget.history()
    with pytest.raises(ValueError,match='BLOCKED_REVIEW'):
        budget.preflight(admission=lambda:interrupted['next_allowed_at'])
    with budget.ownership() as owner:
        result=budget.recover_review_block(expected_head=interrupted['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:3000,ownership=owner)
    assert result['next_allowed_at']==interrupted['next_allowed_at']
    assert budget.history()==saved_history and budget.inspect()['last_outcome']=='INTERRUPTED_UNKNOWN'
    with pytest.raises(ValueError,match='COOLDOWN'):
        budget.preflight(admission=lambda:interrupted['next_allowed_at']-1)
    budget.preflight(admission=lambda:interrupted['next_allowed_at'])


@pytest.mark.parametrize('change,code',[
    ('head','HEAD_DISAGREEMENT'),('account','RECOVERY_SCOPE_DISAGREEMENT'),
    ('review','CONFIRMATION_REQUIRED'),('uncertainty','CONFIRMATION_REQUIRED')])
def test_recovery_scope_or_confirmation_failure_is_byte_preserving(tmp_path,change,code):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget,status_code=401)
    state=budget.inspect();before=budget.path.read_bytes()
    options=dict(expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
        confirm_block_reviewed=True,confirm_uncertainty_preserved=True,admission=lambda:1002)
    if change=='head':options['expected_head']='f'*64
    if change=='account':options['expected_account_id']=OTHER
    if change=='review':options['confirm_block_reviewed']=False
    if change=='uncertainty':options['confirm_uncertainty_preserved']=False
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match=code):
            budget.recover_review_block(**options,ownership=owner)
    assert budget.path.read_bytes()==before and budget.inspect()==state


def test_recovery_is_not_a_cap_reset_or_second_unblock(tmp_path,monkeypatch):
    import nexus.paper_provider_read_budget as subject
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget,status_code=401)
    blocked=budget.inspect()
    monkeypatch.setattr(subject,'MAX_SESSIONS',1)
    with budget.ownership() as owner:
        budget.recover_review_block(expected_head=blocked['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
    recovered=budget.inspect();before=budget.path.read_bytes()
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match='RECOVERY_NOT_REQUIRED'):
            budget.recover_review_block(expected_head=recovered['head_sha256'],
                expected_account_id=ACCOUNT,confirm_block_reviewed=True,
                confirm_uncertainty_preserved=True,admission=lambda:1061,ownership=owner)
    with pytest.raises(ValueError,match='SESSION_CAP'):
        budget.preflight(admission=lambda:1061)
    assert budget.path.read_bytes()==before and recovered['sessions']==1


def test_tampered_chain_and_foreign_provider_are_rejected(tmp_path):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path);complete(budget)
    with pytest.raises(ValueError,match='PROVIDER_OR_SCHEMA'):
        PaperProviderReadBudget(path,'OTHER_PAPER').inspect()
    with sqlite3.connect(path) as db:db.execute("UPDATE events SET previous=? WHERE seq=1",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        budget.inspect()


def test_write_lock_contention_fails_closed_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path);complete(budget)
    expected=budget.inspect();original=path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(path);blocker.execute('BEGIN IMMEDIATE')
    try:
        with budget.ownership() as owner:
            with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_BUDGET_DATABASE_BUSY'):
                budget.begin(account_id=ACCOUNT,account_before_head_sha256='a'*64,
                    operation='BTC_ASSET_RULES',admission=lambda:1061,
                    ownership=owner,expected_head=expected['head_sha256'])
    finally:
        blocker.rollback();blocker.close()
    assert path.read_bytes()==original and budget.inspect()==expected
    complete(budget,at=1061,before='a'*64)


def test_commit_lock_contention_rolls_back_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path);complete(budget)
    expected=budget.inspect();original=path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(path);blocker.execute('BEGIN')
    blocker.execute('SELECT seq FROM events LIMIT 1').fetchone()
    try:
        with budget.ownership() as owner:
            with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_BUDGET_DATABASE_BUSY'):
                budget.begin(account_id=ACCOUNT,account_before_head_sha256='a'*64,
                    operation='BTC_ASSET_RULES',admission=lambda:1061,
                    ownership=owner,expected_head=expected['head_sha256'])
    finally:
        blocker.rollback();blocker.close()
    assert path.read_bytes()==original and budget.inspect()==expected
    complete(budget,at=1061,before='a'*64)


def test_public_read_lock_contention_fails_closed_then_recovers(tmp_path, monkeypatch):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path);complete(budget)
    expected=budget.inspect();original=path.read_bytes()
    monkeypatch.setattr(subject,'DB_BUSY_TIMEOUT_MS',50)
    blocker=sqlite3.connect(path);blocker.execute('BEGIN EXCLUSIVE')
    try:
        with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_BUDGET_DATABASE_BUSY'):
            budget.inspect()
    finally:
        blocker.rollback();blocker.close()
    assert path.read_bytes()==original and budget.inspect()==expected


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
