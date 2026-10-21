"""Durable content-free receipts for complete local mixed-read review."""
import json
import sqlite3

import pytest

from nexus import paper_read_review as review
from nexus.paper_read_review_store import PaperReadReviewStore,ZERO


def complete(tmp_path):
    gate,bindings=review._fixture(tmp_path,full_activity=True)
    return gate,bindings,gate.inspect()['head_sha256']


def string_values(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for key,item in value.items():yield from string_values(key);yield from string_values(item)
    elif isinstance(value,list):
        for item in value:yield from string_values(item)


def test_complete_review_is_retained_without_private_observations(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    receipt=review.retain_review(path,gate,bindings,expected_head=head)
    replay=PaperReadReviewStore(path,gate).inspect()
    assert replay['retained_receipts']==1 and replay['head_sha256']==receipt['head_sha256']
    assert replay['receipts'][0]['scope']['pacing_head_sha256']==head
    content=path.read_bytes()
    for private in (b'fixture_key_only_123',b'fixture_secret_only_123',b'nx_review_fixture',b'BTC/USD'):
        assert private not in content
    with sqlite3.connect(path) as db:
        bodies=[json.loads(row[0]) for row in db.execute('SELECT body FROM records')]
    assert '1000' not in set(string_values(bodies))
    assert not replay['ready_for_routing'] and not replay['release_approved']


def test_exact_duplicate_is_idempotent(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    saved=path.read_bytes()
    second=review.retain_review(path,gate,bindings,expected_head=head,expected_store_head=first['head_sha256'])
    assert second['duplicate'] and second['head_sha256']==first['head_sha256'] and path.read_bytes()==saved


@pytest.mark.parametrize('change',[lambda b:b.pop(),lambda b:b.__setitem__(0,dict(b[0],normalized_result={} ))])
def test_incomplete_or_changed_review_never_creates_store(tmp_path,change):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3';change(bindings)
    with pytest.raises(ValueError,match='PAPER_READ_REVIEW_INCOMPLETE'):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert not path.exists()


def test_stale_store_head_and_revoked_input_do_not_append(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    first=review.retain_review(path,gate,bindings,expected_head=head)
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        review.retain_review(path,gate,bindings,expected_head=head,expected_store_head=ZERO)
    with pytest.raises(ValueError,match='INPUT_REVOKED'):
        review.retain_review(path,gate,bindings,expected_head=head,expected_store_head=first['head_sha256'],precommit_check=lambda:False)
    assert PaperReadReviewStore(path,gate).inspect()['retained_receipts']==1


def test_pacing_path_and_sqlite_companions_are_rejected_before_write(tmp_path):
    gate,bindings,head=complete(tmp_path)
    for path in (gate.path,gate.path.with_name(gate.path.name+'-wal'),gate.path.with_name(gate.path.name+'.owner.lock')):
        with pytest.raises(ValueError,match='RELATED_PATHS_REQUIRED_DISTINCT'):
            review.retain_review(path,gate,bindings,expected_head=head)


def test_tampered_chain_is_rejected(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    review.retain_review(path,gate,bindings,expected_head=head)
    with sqlite3.connect(path) as db:db.execute("UPDATE records SET previous=? WHERE seq=1",('f'*64,))
    with pytest.raises(ValueError,match='CHAIN_DISAGREEMENT'):
        PaperReadReviewStore(path,gate).inspect()


def test_pacing_change_suppresses_retention(tmp_path):
    gate,bindings,head=complete(tmp_path);path=tmp_path/'review.sqlite3'
    gate.begin(admission=lambda:1792369000)
    with pytest.raises(ValueError,match='PAPER_READ_REVIEW_HEAD_CHANGED'):
        review.retain_review(path,gate,bindings,expected_head=head)
    assert not path.exists()
