"""Provider/account read-history composition and durable receipt tests."""
import sqlite3

import pytest

from nexus import paper_provider_read_review as subject,paper_provider_read_review_store as store_subject,paper_read_review
from nexus.alpaca_paper_contract import sha
from nexus.paper_provider_read_budget import PaperProviderReadBudget,ZERO
from nexus.paper_provider_read_review_store import PaperProviderReadReviewStore
from nexus.paper_read_pacing import OPERATION_PATTERNS,PaperReadPacing
from test_alpaca_paper_inspection import ACCOUNT,OTHER


def build(tmp_path,count=5,pending=False):
    gate,_=paper_read_review._fixture(tmp_path/'account')
    history=gate.operation_history();budget=PaperProviderReadBudget(tmp_path/'provider.sqlite3')
    for index,record in enumerate(history[:count]):
        with budget.ownership() as owner:
            token=budget.begin(account_id=ACCOUNT,account_before_head_sha256=record['before_head_sha256'],
                operation=record['operation'],admission=lambda index=index:1000+index*61,ownership=owner)
            budget.finish(token,account_after_head_sha256=record['after_head_sha256'],
                actual_gets=len(record['request_kinds']),admission=lambda index=index:1001+index*61,
                ownership=owner,status_code=200)
    if pending:
        with budget.ownership() as owner:
            budget.begin(account_id=ACCOUNT,account_before_head_sha256=gate.inspect()['head_sha256'],
                operation='ACCOUNT_INVENTORY',admission=lambda:1400,ownership=owner)
    return gate,budget


def heads(gate,budget):
    return dict(expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256']})


def files(root):return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_complete_provider_account_history_matches_without_authority_or_writes(tmp_path):
    gate,budget=build(tmp_path);before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert result['local_provider_account_history_consistent'] and result['all_five_purposes_observed']
    assert result['matched_sessions']==result['provider_sessions_replayed']==result['account_sessions_replayed']==5
    assert result['review_reasons']==[] and result['status']=='REVIEW_REQUIRED'
    assert all(result[key] is False for key in subject.FLAGS) and files(tmp_path)==before


def test_one_provider_history_can_exactly_compose_multiple_account_journals(tmp_path):
    gate,budget=build(tmp_path);other=PaperReadPacing(tmp_path/'other.sqlite3',OTHER)
    kinds=list(OPERATION_PATTERNS['ACCOUNT_INVENTORY'][0])
    receipt=dict(operation='ACCOUNT_INVENTORY',request_kinds=kinds,
        request_sha256=[sha(dict(request=i)) for i in range(len(kinds))],
        response_sha256=[sha(dict(response=i)) for i in range(len(kinds))])
    with other.ownership() as owner:
        token=other.begin(admission=lambda:2000,ownership=owner,operation='ACCOUNT_INVENTORY')
        other.finish(token,admission=lambda:2001,status_code=200,ownership=owner,receipt=receipt)
    record=other.operation_history()[0]
    with budget.ownership() as owner:
        token=budget.begin(account_id=OTHER,account_before_head_sha256=record['before_head_sha256'],
            operation=record['operation'],admission=lambda:1400,ownership=owner)
        budget.finish(token,account_after_head_sha256=record['after_head_sha256'],actual_gets=len(kinds),
            admission=lambda:1401,ownership=owner,status_code=200)
    result=subject.review(budget,[gate,other],expected_provider_head=budget.inspect()['head_sha256'],
        expected_account_heads={gate.account_digest:gate.inspect()['head_sha256'],other.account_digest:other.inspect()['head_sha256']})
    assert result['local_provider_account_history_consistent'] and result['account_journals']==2
    assert result['matched_sessions']==6 and result['operation_counts']['ACCOUNT_INVENTORY']==2


@pytest.mark.parametrize('count,reason',[(4,'PROVIDER_ACCOUNT_SESSION_MISMATCH'),(0,'FIVE_PURPOSE_HISTORY_INCOMPLETE')])
def test_missing_provider_history_never_matches_account_history(tmp_path,count,reason):
    gate,budget=build(tmp_path,count=count)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent'] and reason in result['review_reasons']


def test_pending_provider_history_is_preserved_as_review_block(tmp_path):
    gate,budget=build(tmp_path,pending=True);before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent']
    assert 'PENDING_PROVIDER_HISTORY' in result['review_reasons'] and files(tmp_path)==before


def test_pending_account_history_is_preserved_as_review_block(tmp_path):
    gate,budget=build(tmp_path)
    at=gate.inspect()['next_allowed_at']
    with gate.ownership() as owner:
        gate.begin(admission=lambda:at,ownership=owner,operation='ACCOUNT_INVENTORY')
    before=files(tmp_path)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent']
    assert 'INCOMPLETE_ACCOUNT_HISTORY' in result['review_reasons'] and files(tmp_path)==before


def test_legacy_account_history_is_never_promoted_to_typed_match(tmp_path):
    gate,budget=build(tmp_path)
    at=gate.inspect()['next_allowed_at'];token=gate.begin(admission=lambda:at)
    gate.finish(token,admission=lambda:at+1,status_code=200)
    result=subject.review(budget,[gate],**heads(gate,budget))
    assert not result['local_provider_account_history_consistent']
    assert 'LEGACY_ACCOUNT_HISTORY' in result['review_reasons']


@pytest.mark.parametrize('which',['provider','account'])
def test_stale_exact_heads_fail_before_review(tmp_path,which):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    if which=='provider':options['expected_provider_head']=ZERO
    else:options['expected_account_heads'][gate.account_digest]=ZERO
    with pytest.raises(ValueError,match='HEAD_CHANGED'):subject.review(budget,[gate],**options)


@pytest.mark.parametrize('change',['duplicate','foreign','missing','bad_head'])
def test_account_scope_is_bounded_and_exact(tmp_path,change):
    gate,budget=build(tmp_path);options=heads(gate,budget);gates=[gate]
    if change=='duplicate':gates.append(gate)
    if change=='foreign':options['expected_account_heads']['f'*64]=ZERO
    if change=='missing':options['expected_account_heads']={}
    if change=='bad_head':options['expected_account_heads'][gate.account_digest]=True
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_'):
        subject.review(budget,gates,**options)


def test_durable_receipt_binds_both_heads_without_private_content(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    receipt=subject.retain_review(path,budget,[gate],**heads(gate,budget))
    replay=PaperProviderReadReviewStore(path,budget,[gate]).inspect();content=path.read_bytes()
    assert replay['retained_receipts']==1 and replay['head_sha256']==receipt['head_sha256']
    scope=replay['receipts'][0]['scope']
    assert scope['provider_head_sha256']==budget.inspect()['head_sha256']
    assert scope['sessions_replayed']==scope['matched_sessions']==5
    assert ACCOUNT.encode() not in content and b'BTC/USD' not in content
    assert not replay['ready_for_routing'] and not replay['release_approved']


def test_exact_duplicate_is_idempotent_and_revocation_fails_closed(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3';options=heads(gate,budget)
    first=subject.retain_review(path,budget,[gate],**options);saved=path.read_bytes()
    second=subject.retain_review(path,budget,[gate],expected_store_head=first['head_sha256'],**options)
    assert second['duplicate'] and path.read_bytes()==saved
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        subject.retain_review(path,budget,[gate],expected_store_head=first['head_sha256'],precommit_check=lambda:False,**options)
    assert path.read_bytes()==saved


def test_store_rejects_non_digest_head_with_store_scoped_error(tmp_path):
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_STORE_DIGEST_INVALID'):
        store_subject._digest(True)


def test_incomplete_review_never_creates_receipt_store(tmp_path):
    gate,budget=build(tmp_path,count=4);path=tmp_path/'review.sqlite3'
    with pytest.raises(ValueError,match='PAPER_PROVIDER_READ_REVIEW_INCOMPLETE'):
        subject.retain_review(path,budget,[gate],**heads(gate,budget))
    assert not path.exists()


def test_store_path_cannot_collide_with_any_source_journal(tmp_path):
    gate,budget=build(tmp_path);options=heads(gate,budget)
    for path in (gate.path,budget.path,budget.path.with_name(budget.path.name+'-wal')):
        with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
            subject.retain_review(path,budget,[gate],**options)


def test_tampered_receipt_chain_is_rejected(tmp_path):
    gate,budget=build(tmp_path);path=tmp_path/'review.sqlite3'
    subject.retain_review(path,budget,[gate],**heads(gate,budget))
    with sqlite3.connect(path) as db:db.execute("UPDATE records SET previous=? WHERE seq=1",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        PaperProviderReadReviewStore(path,budget,[gate]).inspect()
