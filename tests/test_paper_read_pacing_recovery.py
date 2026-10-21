"""Reviewed account pacing recovery uses disposable local journals only."""
import subprocess
import sys

import pytest

from nexus.paper_read_pacing import PaperReadPacing
from nexus.paper_read_pacing_recovery import recover
from test_alpaca_paper_inspection import ACCOUNT,OTHER


def blocked(tmp_path,status=401):
    gate=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    token=gate.begin(admission=lambda:1000)
    gate.finish(token,admission=lambda:1001,status_code=status)
    return gate


@pytest.mark.parametrize('status,outcome',[(401,'AUTH_DENIED'),(302,'REVIEW_REQUIRED')])
def test_exact_reviewed_recovery_preserves_history_cooldown_and_capacity(tmp_path,status,outcome):
    gate=blocked(tmp_path,status);before=gate.inspect();history=gate.operation_history()
    with gate.ownership() as owner:
        result=gate.recover_review_block(expected_head=before['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
    after=gate.inspect()
    assert result['mode']=='PAPER_READ_PACING_REVIEWED_RECOVERY'
    assert result['blocked_outcome']==outcome and result['review_block_cleared']
    assert result['uncertainty_preserved'] and not result['cooldown_shortened']
    assert not result['history_rewritten'] and not result['session_capacity_reclaimed']
    assert after['recoveries']==1 and not after['review_required']
    assert after['sessions']==before['sessions']==1 and after['next_allowed_at']==before['next_allowed_at']
    assert gate.operation_history()==history
    with pytest.raises(ValueError,match='COOLDOWN'):
        gate.begin(admission=lambda:before['next_allowed_at']-1)
    assert gate.begin(admission=lambda:before['next_allowed_at'])


def test_interrupted_unknown_stays_blocked_until_explicit_recovery(tmp_path):
    gate=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT)
    with gate.ownership() as owner:
        token=gate.begin(admission=lambda:1000,ownership=owner,operation='BTC_QUOTE_OBSERVATION')
    pending=gate.inspect()
    gate.interrupt(token,expected_head=pending['head_sha256'],expected_account_id=ACCOUNT,
        confirm_interrupted=True,admission=lambda:1001)
    interrupted=gate.inspect();history=gate.operation_history()
    assert interrupted['review_required'] and interrupted['next_allowed_at']==87401
    with pytest.raises(ValueError,match='BLOCKED_REVIEW'):
        gate.begin(admission=lambda:87401)
    with gate.ownership() as owner:
        result=gate.recover_review_block(expected_head=interrupted['head_sha256'],
            expected_account_id=ACCOUNT,confirm_block_reviewed=True,
            confirm_uncertainty_preserved=True,admission=lambda:1002,ownership=owner)
    assert result['next_allowed_at']==87401
    assert gate.inspect()['last_outcome']=='INTERRUPTED_UNKNOWN'
    assert gate.operation_history()==history
    with pytest.raises(ValueError,match='COOLDOWN'):
        gate.begin(admission=lambda:87400)
    assert gate.begin(admission=lambda:87401)


@pytest.mark.parametrize('change,code',[
    ('head','HEAD_DISAGREEMENT'),('account','ACCOUNT_DISAGREEMENT'),
    ('review','CONFIRMATION_REQUIRED'),('uncertainty','CONFIRMATION_REQUIRED')])
def test_scope_and_confirmation_failures_preserve_exact_bytes(tmp_path,change,code):
    gate=blocked(tmp_path);state=gate.inspect();before=gate.path.read_bytes()
    options=dict(expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
        confirm_block_reviewed=True,confirm_uncertainty_preserved=True,admission=lambda:1002)
    if change=='head':options['expected_head']='f'*64
    if change=='account':options['expected_account_id']=OTHER
    if change=='review':options['confirm_block_reviewed']=False
    if change=='uncertainty':options['confirm_uncertainty_preserved']=False
    with gate.ownership() as owner:
        with pytest.raises(ValueError,match=code):
            gate.recover_review_block(**options,ownership=owner)
    assert gate.path.read_bytes()==before and gate.inspect()==state


def test_recovery_is_not_a_cap_reset_or_second_unblock(tmp_path,monkeypatch):
    import nexus.paper_read_pacing as subject
    gate=blocked(tmp_path);state=gate.inspect();monkeypatch.setattr(subject,'MAX_SESSIONS',1)
    with gate.ownership() as owner:
        gate.recover_review_block(expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
            confirm_block_reviewed=True,confirm_uncertainty_preserved=True,
            admission=lambda:1002,ownership=owner)
    after=gate.inspect();before=gate.path.read_bytes()
    with gate.ownership() as owner:
        with pytest.raises(ValueError,match='RECOVERY_NOT_REQUIRED'):
            gate.recover_review_block(expected_head=after['head_sha256'],expected_account_id=ACCOUNT,
                confirm_block_reviewed=True,confirm_uncertainty_preserved=True,
                admission=lambda:1061,ownership=owner)
    with pytest.raises(ValueError,match='SESSION_CAP'):gate.begin(admission=lambda:1061)
    assert gate.path.read_bytes()==before


def test_cli_missing_and_private_failures_are_sanitized_without_creation(tmp_path):
    missing=tmp_path/'missing'/'pacing.sqlite3'
    result=subprocess.run([sys.executable,'-m','nexus.paper_read_pacing_recovery',
        '--pacing',str(missing),'--expected-head','0'*64,'--expected-account',ACCOUNT,
        '--confirm-reviewed-block','--confirm-preserve-uncertainty'],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==2 and result.stdout.strip()=='PAPER_READ_PACING_RECOVERY_FAILED'
    assert not result.stderr and not missing.parent.exists()


def test_recovery_wrapper_requires_existing_exact_source(tmp_path):
    gate=blocked(tmp_path);state=gate.inspect()
    result=recover(gate.path,expected_head=state['head_sha256'],expected_account_id=ACCOUNT,
        confirm_block_reviewed=True,confirm_uncertainty_preserved=True,admission=lambda:1002)
    assert result['review_block_cleared'] and gate.inspect()['recoveries']==1
