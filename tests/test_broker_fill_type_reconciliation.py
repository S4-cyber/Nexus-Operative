"""Contradictory retained fill semantics never establish local agreement."""
from nexus import broker_request_activity_review
from nexus.broker_activity_store import BrokerActivityStore
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_contract import plan, response
from test_broker_request_journal import ACCOUNT


def test_terminal_fill_with_remainder_blocks_request_activity_agreement(
        tmp_path):
    request_journal = BrokerRequestJournal(tmp_path / 'requests.sqlite3',
        ACCOUNT)
    activity_store = BrokerActivityStore(tmp_path / 'activities.sqlite3',
        ACCOUNT)
    preview = plan()
    request_journal.prepare(preview)
    request_journal.mark_submission_uncertain('nx_fixture')
    observed = response(1, status='canceled', filled_qty='1',
        filled_avg_price='99')
    request_journal.observe('nx_fixture', observed)
    order = request_journal.recovery()['requests'][0]['snapshot']
    activity_store.retain([dict(
        id='20261017180000000::01234567-89ab-cdef-0123-000000000001',
        activity_type='FILL', order_id=observed['id'], symbol='BTCUSD',
        side='buy', type='fill', qty='1', price='99', cum_qty='1',
        leaves_qty='1', transaction_time='2026-10-17T18:00:01Z')],
        [order], after='2026-10-17T00:00:00Z',
        until='2026-10-18T00:00:00Z')

    result = broker_request_activity_review.review(request_journal,
        activity_store,
        expected_request_head=request_journal.recovery()['head_sha256'],
        expected_activity_head=activity_store.inspect()['head_sha256'])

    assert not result['local_request_activity_agreement']
    assert result['agreement_blockers'] == [
        'FILL_TYPE_REMAINDER_DISAGREEMENT']
    assert result['request_reviews'][0]['local_terms_agree']
    assert all(result[flag] is False
        for flag in broker_request_activity_review.FLAGS)
