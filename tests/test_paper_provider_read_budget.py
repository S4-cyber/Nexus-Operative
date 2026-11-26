"""Local provider-scope paper-read budget and conservative composition."""
import os
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


def test_failed_first_transaction_restores_absence_and_exact_retry(tmp_path):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path)
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match='HEAD_CHANGED'):
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
                operation='BTC_ASSET_RULES',admission=lambda:1000,
                ownership=owner,expected_head='f'*64)
    assert not path.exists()
    assert all(not path.with_name(path.name+suffix).exists() for suffix in
        ('-journal','-wal','-shm'))
    complete(budget)
    assert path.is_file() and budget.inspect()['completed_sessions']==1


def test_constructor_pins_parent_alias_before_first_write(tmp_path):
    original=tmp_path/'original';replacement=tmp_path/'replacement'
    original.mkdir();replacement.mkdir();alias=tmp_path/'selected'
    try:alias.symlink_to(original,target_is_directory=True)
    except OSError:pytest.skip('directory symlink unavailable')
    budget=PaperProviderReadBudget(alias/'provider.sqlite3')
    alias.unlink();alias.symlink_to(replacement,target_is_directory=True)
    assert budget.path==original.resolve()/'provider.sqlite3'
    complete(budget)
    assert budget.path.is_file()
    assert not (replacement/'provider.sqlite3').exists()


def test_first_write_cleanup_uses_captured_path_after_object_retarget(
        tmp_path,monkeypatch):
    path=tmp_path/'provider.sqlite3';decoy=tmp_path/'decoy.sqlite3'
    decoy.write_bytes(b'do-not-delete');budget=PaperProviderReadBudget(path)
    def connect(*_args,**_kwargs):
        budget.path=decoy
        raise sqlite3.OperationalError('injected retargeted connect failure')
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
                operation='BTC_ASSET_RULES',admission=lambda:1000,
                ownership=owner)
    assert not path.exists()
    assert decoy.read_bytes()==b'do-not-delete'


def test_first_write_refuses_main_file_replacement_before_sqlite_setup(
        tmp_path,monkeypatch):
    path=tmp_path/'provider.sqlite3';replacement=tmp_path/'replacement.sqlite3'
    sqlite3.connect(replacement).close();replacement_bytes=replacement.read_bytes()
    budget=PaperProviderReadBudget(path);original=subject.sqlite3.connect
    def replace_then_connect(*args,**kwargs):
        os.replace(replacement,path)
        return original(*args,**kwargs)
    monkeypatch.setattr(subject.sqlite3,'connect',replace_then_connect)
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
                operation='BTC_ASSET_RULES',admission=lambda:1000,
                ownership=owner)
    assert path.read_bytes()==replacement_bytes


def test_read_refuses_valid_foreign_sqlite_connection(tmp_path,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget)
    foreign=PaperProviderReadBudget(tmp_path/'foreign.sqlite3');complete(foreign)
    original=subject.sqlite3.connect;before=budget.path.read_bytes()
    def connect_foreign(*_args,**_kwargs):
        return original(foreign.path)
    monkeypatch.setattr(subject.sqlite3,'connect',connect_foreign)
    with pytest.raises(ValueError,match='CONNECTION_BINDING_CHANGED'):
        budget.inspect()
    assert budget.path.read_bytes()==before


def test_active_ownership_retarget_rolls_back_exact_first_write(tmp_path):
    path=tmp_path/'provider.sqlite3';decoy=tmp_path/'decoy.sqlite3'
    budget=PaperProviderReadBudget(path)
    def retarget():
        budget.path=decoy
        return 1000
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
                operation='BTC_ASSET_RULES',admission=retarget,
                ownership=owner)
    assert not path.exists() and not decoy.exists()


def test_existing_write_context_rejects_uncommitted_return(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget)
    before=budget.path.read_bytes()
    with pytest.raises(ValueError,match='UNCOMMITTED_TRANSACTION'):
        with budget._write_connection():
            pass
    assert budget.path.read_bytes()==before


def test_committed_write_ack_rechecks_main_identity(tmp_path,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget)
    head=budget.inspect()['head_sha256'];armed=False
    original=subject._require_file_identity
    def require(path,identity):
        original(path,identity)
        if armed:subject._fail('FILE_CHANGED')
    monkeypatch.setattr(subject,'_require_file_identity',require)
    def admission():
        nonlocal armed
        armed=True
        return 1061
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match='FILE_CHANGED'):
            budget.begin(account_id=ACCOUNT,
                account_before_head_sha256='b'*64,
                operation='ACCOUNT_INVENTORY',admission=admission,
                ownership=owner,expected_head=head)
    armed=False
    state=budget.inspect()
    assert state['sessions']==2 and state['pending_token'] is not None
    assert state['ready_for_routing'] is False


def _continuity_connection(counter,path=None):
    class ContinuityConnection(sqlite3.Connection):
        def serialize(self,*args,**kwargs):
            result=super().serialize(*args,**kwargs)
            counter.append(len(result));return result

        def close(self):
            super().close()
            if path is not None:
                observed=path.stat()
                os.utime(path,ns=(observed.st_atime_ns,
                    observed.st_mtime_ns+1_000_000_000))
    return ContinuityConnection


def test_committed_write_ack_rechecks_main_content(tmp_path,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget)
    head=budget.inspect()['head_sha256'];counter=[]
    original_connect=subject.sqlite3.connect
    original_fingerprint=subject._main_fingerprint
    def connect(database,*args,**kwargs):
        if '?mode=rw' in str(database):
            kwargs['factory']=_continuity_connection(counter)
        return original_connect(database,*args,**kwargs)
    def changed(path):
        result=original_fingerprint(path)
        if counter:
            altered=list(result);altered[7]='f'*64;return tuple(altered)
        return result
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    monkeypatch.setattr(subject,'_main_fingerprint',changed)
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match='CONNECTION_CONTENT_CHANGED'):
            budget.begin(account_id=ACCOUNT,
                account_before_head_sha256='b'*64,
                operation='ACCOUNT_INVENTORY',admission=lambda:1061,
                ownership=owner,expected_head=head)
    assert len(counter)==1
    monkeypatch.setattr(subject,'_main_fingerprint',original_fingerprint)
    state=budget.inspect()
    assert state['sessions']==2 and state['pending_token'] is not None
    assert state['ready_for_routing'] is False


def test_committed_write_ack_rechecks_content_after_close(tmp_path,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget)
    head=budget.inspect()['head_sha256'];counter=[]
    original=subject.sqlite3.connect
    def connect(database,*args,**kwargs):
        if '?mode=rw' in str(database):
            kwargs['factory']=_continuity_connection(counter,budget.path)
        return original(database,*args,**kwargs)
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    with budget.ownership() as owner:
        with pytest.raises(ValueError,match='FILE_SET_CHANGED'):
            budget.begin(account_id=ACCOUNT,
                account_before_head_sha256='b'*64,
                operation='ACCOUNT_INVENTORY',admission=lambda:1061,
                ownership=owner,expected_head=head)
    state=budget.inspect()
    assert len(counter)==1 and state['sessions']==2
    assert state['pending_token'] is not None and not state['ready_for_routing']


def test_uncommitted_write_never_serializes_for_acknowledgement(
        tmp_path,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3');complete(budget)
    original=subject.sqlite3.connect
    class NoSerialize(sqlite3.Connection):
        def serialize(self,*_args,**_kwargs):
            raise AssertionError('uncommitted bytes must not be acknowledged')
    def connect(database,*args,**kwargs):
        if '?mode=rw' in str(database):kwargs['factory']=NoSerialize
        return original(database,*args,**kwargs)
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    with pytest.raises(ValueError,match='UNCOMMITTED_TRANSACTION'):
        with budget._write_connection():pass


def test_main_replacement_during_transaction_cannot_acknowledge(
        tmp_path):
    path=tmp_path/'provider.sqlite3';replacement=tmp_path/'replacement.sqlite3'
    budget=PaperProviderReadBudget(path);complete(budget)
    head=budget.inspect()['head_sha256']
    with sqlite3.connect(replacement) as db:
        db.execute('CREATE TABLE decoy (value TEXT)')
        db.execute('INSERT INTO decoy VALUES (\'retain\')')
    replacement_bytes=replacement.read_bytes()
    def admission():
        try:os.replace(replacement,path)
        except OSError:pytest.skip('open SQLite main replacement denied')
        return 1061
    with pytest.raises(ValueError,match='FILE_CHANGED'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,
                account_before_head_sha256='b'*64,
                operation='ACCOUNT_INVENTORY',admission=admission,
                ownership=owner,expected_head=head)
    assert path.read_bytes()==replacement_bytes


@pytest.mark.parametrize('field',('provider_scope','provider_digest'))
def test_active_ownership_rejects_provider_scope_mutation(tmp_path,field):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path)
    def mutate():
        setattr(budget,field,'OTHER_PAPER' if field=='provider_scope' else 'f'*64)
        return 1000
    with pytest.raises(ValueError,match='STORE_BINDING_CHANGED'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
                operation='BTC_ASSET_RULES',admission=mutate,
                ownership=owner)
    assert not path.exists()


def test_fresh_connection_failure_restores_absence_and_exact_retry(
        tmp_path,monkeypatch):
    path=tmp_path/'provider.sqlite3';budget=PaperProviderReadBudget(path)
    original=subject.sqlite3.connect;failed=False
    def connect(*args,**kwargs):
        nonlocal failed
        if not failed:
            failed=True
            raise sqlite3.OperationalError('injected first connect failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(subject.sqlite3,'connect',connect)
    with pytest.raises(sqlite3.OperationalError,
            match='injected first connect failure'):
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=ZERO,
                operation='BTC_ASSET_RULES',admission=lambda:1000,
                ownership=owner)
    assert failed and not path.exists()
    complete(budget)
    assert path.is_file() and budget.inspect()['completed_sessions']==1


def test_uncommitted_cleanup_never_deletes_ambiguous_state(tmp_path):
    path=tmp_path/'provider.sqlite3';path.touch(mode=0o600)
    observed=path.lstat()
    identity=(str(path.resolve()),observed.st_dev,observed.st_ino)

    replacement=tmp_path/'replacement.sqlite3'
    replacement.touch(mode=0o600);os.replace(replacement,path)
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        subject._discard_uncommitted_new(path,identity)
    assert path.exists()

    observed=path.lstat()
    identity=(str(path.resolve()),observed.st_dev,observed.st_ino)
    sidecar=path.with_name(path.name+'-journal');sidecar.write_bytes(b'private')
    with pytest.raises(ValueError,match='UNCOMMITTED_CLEANUP_UNCERTAIN'):
        subject._discard_uncommitted_new(path,identity)
    assert path.exists() and sidecar.read_bytes()==b'private'


@pytest.mark.parametrize('body_error',(False,True))
def test_provider_ownership_rechecks_lock_path_on_context_exit(
        tmp_path,body_error,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    lock_path=budget.path.resolve().with_name(budget.path.name+'.owner.lock')
    displaced=tmp_path/'displaced-provider-owner.lock'
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_READ_BUDGET_OWNER_FILE_CHANGED'):
        with budget.ownership() as owner:
            assert owner.identity()
            if os.name=='nt':
                monkeypatch.setattr(owner,'validate',
                    lambda:subject._fail('OWNER_FILE_CHANGED'))
            else:
                lock_path.rename(displaced)
                lock_path.write_bytes(b'0')
            if body_error:
                raise RuntimeError('private lower context failure')
    assert lock_path.is_file()
    assert displaced.is_file() is (os.name!='nt')


@pytest.mark.parametrize('body_error',(False,True))
def test_provider_ownership_normalizes_missing_path_on_context_exit(
        tmp_path,body_error,monkeypatch):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    original=subject.Path.stat
    with pytest.raises(ValueError,
            match='PAPER_PROVIDER_READ_BUDGET_OWNER_FILE_CHANGED'):
        with budget.ownership() as owner:
            def missing(path,*args,**kwargs):
                if path==owner.path:
                    raise FileNotFoundError('private lock path')
                return original(path,*args,**kwargs)
            monkeypatch.setattr(subject.Path,'stat',missing)
            if body_error:
                raise RuntimeError('private lower context failure')


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


def test_recovery_receipt_cold_replays_exact_conservative_state(tmp_path):
    budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    complete(budget,status_code=401)
    blocked=budget.inspect();history=budget.history()
    with budget.ownership() as owner:
        result=budget.recover_review_block(expected_head=blocked['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,
            ownership=owner)
        receipt=budget.recovery_receipt()
    assert receipt['head_sha256']==receipt['recovery_head_sha256']==result['head_sha256']
    assert receipt['blocked_session_head_sha256']==history[-1]['after_head_sha256']
    assert receipt['blocked_outcome']=='AUTH_DENIED'
    assert receipt['recoveries']==1 and receipt['sessions']==1
    assert receipt['review_required'] is False and receipt['pending_token'] is None
    assert receipt['next_allowed_at']==blocked['next_allowed_at']
    assert receipt['exact_recovery_replayed'] and receipt['read_only']
    assert all(receipt[key] is False for key in ('history_rewritten',
        'session_capacity_reclaimed','admission_reserved','automatic_retry',
        'automatic_continuation','ready_for_routing','live_orders_enabled',
        'money_movement_enabled','release_approved'))


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
